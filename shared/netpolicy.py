"""Единый исходящий HTTP-клиент с политикой сетевой безопасности (ТЗ §5.3, AC-02).

Все запросы по адресам, на которые влияет пользователь (обход, robots.txt, sitemap,
проверка ссылок, проверка публикации, подключение CMS), выполняются только через
`SafeHttpClient.fetch`. Проверки привязаны к моменту соединения, а не к строке адреса:

* схема только http/https, порты из белого списка, без userinfo, без обратных косых черт;
* имя узла нормализуется (IDNA, регистр, точка в конце); числовые формы IP
  (`2130706433`, `0x7f.1`) отклоняются как неоднозначные;
* имя разрешается в адреса один раз, в момент соединения, и **все** найденные адреса
  должны быть публичными (смешанный ответ DNS отклоняется целиком); соединение идёт
  на проверенный IP, а TLS проверяет имя исходного узла, поэтому DNS rebinding между
  проверкой и соединением невозможен;
* адреса IPv4-mapped, 6to4, Teredo и NAT64 разворачиваются и проверяются как IPv4;
* каждое перенаправление проходит ту же проверку заново, число переходов ограничено;
* ограничены время соединения, чтения и всего запроса, размер тела (в том числе после
  распаковки), длина адреса.

Сбой DNS означает отказ, а не разрешение. Любой отказ политики поднимает `NetPolicyError`
со стабильным кодом `code`.
"""
from __future__ import annotations

import asyncio
import ipaddress
import os
import re
import socket
import ssl
import time
import zlib
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional
from urllib.parse import urljoin, urlsplit

import httpcore

# Коды ошибок. Первые восемь — отказы политики безопасности, остальные — сбои передачи.
POLICY_CODES = frozenset(
    {
        "invalid_url",
        "scheme_not_allowed",
        "port_not_allowed",
        "userinfo_not_allowed",
        "host_blocked",
        "dns_failure",
        "address_blocked",
        "redirect_limit",
        "redirect_loop",
        "redirect_invalid",
        "response_too_large",
        "method_not_allowed",
    }
)
TRANSPORT_CODES = frozenset({"timeout", "connect_error", "tls_error", "protocol_error", "unsupported_encoding"})
RETRYABLE_CODES = frozenset({"timeout", "connect_error"})

_MAX_URL_LENGTH = 2048
_BLOCKED_HOST_SUFFIXES = (".localhost", ".local", ".internal", ".localdomain")
_NUMERIC_HOST = re.compile(r"^[0-9a-fx.]+$", re.IGNORECASE)
_FORBIDDEN_URL_CHARS = re.compile(r"[\x00-\x20\x7f\\]")
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})

Resolver = Callable[[str, int], Awaitable[list[str]]]


class NetPolicyError(Exception):
    """Запрос отклонён политикой или не удался при передаче."""

    def __init__(self, code: str, message: str = "", url: str = ""):
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code
        self.url = url

    @property
    def is_policy_block(self) -> bool:
        return self.code in POLICY_CODES

    @property
    def retryable(self) -> bool:
        return self.code in RETRYABLE_CODES


@dataclass(frozen=True)
class NetPolicy:
    allowed_schemes: frozenset[str] = frozenset({"http", "https"})
    allowed_ports: frozenset[int] = frozenset({80, 443})
    max_redirects: int = 5
    connect_timeout: float = 3.0
    read_timeout: float = 10.0
    total_timeout: float = 30.0
    max_body_bytes: int = 2 * 1024 * 1024
    user_agent: str = "SEOMasterBot/1.0"
    # Только для изолированного тестового профиля (NETPOLICY_PROFILE=test): точные имена
    # узлов, которым разрешён приватный адрес. В любом другом окружении список должен быть пуст.
    allow_private_hosts: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self):
        if self.allow_private_hosts and os.environ.get("NETPOLICY_PROFILE") != "test":
            raise ValueError("allow_private_hosts разрешён только при NETPOLICY_PROFILE=test")
        if self.max_redirects < 0 or self.max_body_bytes <= 0:
            raise ValueError("invalid policy limits")


@dataclass(frozen=True)
class Target:
    scheme: str
    host: str  # ASCII, нижний регистр, без точки в конце
    port: int
    path_and_query: str

    @property
    def url(self) -> str:
        default = 443 if self.scheme == "https" else 80
        host = f"[{self.host}]" if ":" in self.host else self.host
        netloc = host if self.port == default else f"{host}:{self.port}"
        return f"{self.scheme}://{netloc}{self.path_and_query}"

    @property
    def host_header(self) -> str:
        default = 443 if self.scheme == "https" else 80
        host = f"[{self.host}]" if ":" in self.host else self.host
        return host if self.port == default else f"{host}:{self.port}"


@dataclass
class FetchResult:
    url: str
    final_url: str
    status: int
    headers: dict[str, str]
    body: bytes
    redirects: list[str]
    peer_ip: Optional[str]
    elapsed: float


# --- проверка адресов --------------------------------------------------------

def _embedded_ipv4(ip: ipaddress.IPv6Address) -> list[ipaddress.IPv4Address]:
    found: list[ipaddress.IPv4Address] = []
    if ip.ipv4_mapped is not None:
        found.append(ip.ipv4_mapped)
    if ip.sixtofour is not None:
        found.append(ip.sixtofour)
    if ip.teredo is not None:
        found.extend(ip.teredo)
    packed = ip.packed
    if packed[:12] == bytes.fromhex("0064ff9b0000000000000000"):  # NAT64 64:ff9b::/96
        found.append(ipaddress.IPv4Address(packed[12:]))
    if packed[:12] == bytes(12):  # IPv4-compatible ::a.b.c.d
        found.append(ipaddress.IPv4Address(packed[12:]))
    return found


def is_public_ip(value: str) -> bool:
    """True только для глобально маршрутизируемых публичных адресов."""
    try:
        ip = ipaddress.ip_address(value.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        for embedded in _embedded_ipv4(ip):
            if not embedded.is_global:
                return False
    if not ip.is_global:
        return False
    return not (ip.is_multicast or ip.is_reserved or ip.is_unspecified or ip.is_loopback or ip.is_link_local)


# --- разбор адреса -----------------------------------------------------------

def parse_target(url: str, policy: NetPolicy) -> Target:
    if not isinstance(url, str) or not url or len(url) > _MAX_URL_LENGTH:
        raise NetPolicyError("invalid_url", "empty or too long", str(url)[:200])
    if _FORBIDDEN_URL_CHARS.search(url):
        raise NetPolicyError("invalid_url", "control characters, spaces or backslash", url[:200])
    try:
        parts = urlsplit(url)
        port = parts.port
        hostname = parts.hostname
    except ValueError as exc:
        raise NetPolicyError("invalid_url", str(exc), url[:200]) from exc

    scheme = (parts.scheme or "").lower()
    if scheme not in policy.allowed_schemes:
        raise NetPolicyError("scheme_not_allowed", scheme or "none", url[:200])
    if "@" in parts.netloc:
        raise NetPolicyError("userinfo_not_allowed", "", url[:200])
    if not hostname:
        raise NetPolicyError("invalid_url", "no host", url[:200])

    host = hostname.lower().rstrip(".")
    if not host:
        raise NetPolicyError("invalid_url", "empty host", url[:200])
    is_ip_literal = False
    try:
        ipaddress.ip_address(host.split("%", 1)[0])
        is_ip_literal = True
    except ValueError:
        pass
    if not is_ip_literal:
        try:
            host = host.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise NetPolicyError("invalid_url", "bad IDNA host", url[:200]) from exc
        if not re.fullmatch(r"[a-z0-9_]([a-z0-9_.-]*[a-z0-9_])?", host):
            raise NetPolicyError("invalid_url", "bad characters in host", url[:200])
        if _NUMERIC_HOST.match(host):
            # '2130706433', '0x7f.1', '127.1': разные библиотеки читают их по-разному.
            raise NetPolicyError("host_blocked", "ambiguous numeric host", url[:200])
        if host == "localhost" or host.endswith(_BLOCKED_HOST_SUFFIXES):
            if host not in policy.allow_private_hosts:
                raise NetPolicyError("host_blocked", "reserved host name", url[:200])

    effective_port = port or (443 if scheme == "https" else 80)
    if effective_port not in policy.allowed_ports:
        raise NetPolicyError("port_not_allowed", str(effective_port), url[:200])

    path = parts.path or "/"
    path_and_query = path + (f"?{parts.query}" if parts.query else "")
    return Target(scheme=scheme, host=host, port=effective_port, path_and_query=path_and_query)


# --- разрешение имён и соединение -------------------------------------------

async def default_resolver(host: str, port: int) -> list[str]:
    loop = asyncio.get_running_loop()
    infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    seen: list[str] = []
    for _family, _type, _proto, _canon, sockaddr in infos:
        ip = sockaddr[0].split("%", 1)[0]
        if ip not in seen:
            seen.append(ip)
    return seen


async def resolve_public(host: str, port: int, policy: NetPolicy, resolver: Resolver) -> list[str]:
    """Возвращает адреса узла. Если хотя бы один адрес непубличный, отказ для всего узла."""
    try:
        ipaddress.ip_address(host.split("%", 1)[0])
        addresses = [host.split("%", 1)[0]]
    except ValueError:
        try:
            addresses = await resolver(host, port)
        except Exception as exc:  # любой сбой резолвера означает отказ, а не разрешение
            raise NetPolicyError("dns_failure", type(exc).__name__, host) from exc
    if not addresses:
        raise NetPolicyError("dns_failure", "no addresses", host)
    if host in policy.allow_private_hosts:
        return addresses
    for address in addresses:
        if not is_public_ip(address):
            raise NetPolicyError("address_blocked", f"{host} -> {address}", host)
    return addresses


class _GuardedBackend(httpcore.AsyncNetworkBackend):
    """Сетевой бэкенд httpcore: разрешает имя и проверяет адрес в момент соединения."""

    def __init__(self, policy: NetPolicy, resolver: Resolver, inner: httpcore.AsyncNetworkBackend):
        self._policy = policy
        self._resolver = resolver
        self._inner = inner
        self.peer_ip: Optional[str] = None

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        addresses = await resolve_public(host, port, self._policy, self._resolver)
        last_error: Optional[Exception] = None
        for address in addresses:
            try:
                stream = await self._inner.connect_tcp(
                    address, port, timeout=timeout, local_address=local_address, socket_options=socket_options
                )
                self.peer_ip = address
                return stream
            except (httpcore.ConnectError, httpcore.ConnectTimeout, OSError) as exc:
                last_error = exc
        assert last_error is not None
        raise last_error

    async def connect_unix_socket(self, path, timeout=None, socket_options=None):
        raise NetPolicyError("scheme_not_allowed", "unix sockets are not allowed")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


# --- клиент ------------------------------------------------------------------

def _map_transport_error(exc: BaseException, url: str) -> NetPolicyError:
    chain: list[BaseException] = []
    current: Optional[BaseException] = exc
    while current is not None and current not in chain:
        chain.append(current)
        current = current.__cause__ or current.__context__
    if any(isinstance(item, ssl.SSLError) for item in chain):
        return NetPolicyError("tls_error", "TLS handshake or certificate error", url)
    if isinstance(exc, (httpcore.ConnectTimeout, httpcore.ReadTimeout, httpcore.WriteTimeout, httpcore.PoolTimeout, asyncio.TimeoutError, TimeoutError)):
        return NetPolicyError("timeout", type(exc).__name__, url)
    if isinstance(exc, httpcore.ProtocolError):
        return NetPolicyError("protocol_error", str(exc)[:100], url)
    if isinstance(exc, (httpcore.ConnectError, OSError)):
        return NetPolicyError("connect_error", type(exc).__name__, url)
    return NetPolicyError("protocol_error", type(exc).__name__, url)


async def _read_limited(response, headers: dict[str, str], limit: int, url: str) -> bytes:
    declared = headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit and not headers.get("content-encoding"):
        raise NetPolicyError("response_too_large", f"content-length {declared}", url)
    encoding = headers.get("content-encoding", "identity").lower().strip()
    if encoding in ("", "identity"):
        decoder = None
    elif encoding in ("gzip", "x-gzip", "deflate"):
        decoder = zlib.decompressobj(zlib.MAX_WBITS | 32)
    else:
        raise NetPolicyError("unsupported_encoding", encoding, url)

    raw_total = 0
    out = bytearray()
    async for chunk in response.aiter_stream():
        raw_total += len(chunk)
        if raw_total > limit and decoder is None:
            raise NetPolicyError("response_too_large", f"more than {limit} bytes", url)
        if decoder is None:
            out += chunk
            continue
        if raw_total > limit:
            raise NetPolicyError("response_too_large", f"compressed body over {limit} bytes", url)
        try:
            pending = chunk
            while pending:
                data = decoder.decompress(pending, max(1, limit - len(out) + 1))
                out += data
                if len(out) > limit:
                    raise NetPolicyError("response_too_large", f"decompressed body over {limit} bytes", url)
                pending = decoder.unconsumed_tail
        except zlib.error as exc:
            raise NetPolicyError("protocol_error", "bad compressed body", url) from exc
    if decoder is not None:
        try:
            tail = decoder.flush(max(1, limit - len(out) + 1))
        except zlib.error as exc:
            raise NetPolicyError("protocol_error", "bad compressed body", url) from exc
        out += tail
        if len(out) > limit:
            raise NetPolicyError("response_too_large", f"decompressed body over {limit} bytes", url)
    return bytes(out)


class SafeHttpClient:
    """Клиент с политикой. Один экземпляр можно использовать из нескольких задач."""

    def __init__(
        self,
        policy: Optional[NetPolicy] = None,
        resolver: Optional[Resolver] = None,
        network_backend: Optional[httpcore.AsyncNetworkBackend] = None,
    ):
        self.policy = policy or NetPolicy()
        self._resolver = resolver or default_resolver
        self._inner_backend = network_backend

    @property
    def resolver(self) -> Resolver:
        """Распознаватель имён клиента: нужен предварительным проверкам, чтобы они совпадали с политикой."""
        return self._resolver

    async def fetch(
        self,
        url: str,
        *,
        method: str = "GET",
        headers: Optional[dict[str, str]] = None,
        policy: Optional[NetPolicy] = None,
    ) -> FetchResult:
        policy = policy or self.policy
        method = method.upper()
        if method not in ("GET", "HEAD"):
            raise NetPolicyError("method_not_allowed", method, url)
        started = time.monotonic()
        try:
            return await asyncio.wait_for(
                self._fetch(url, method, headers or {}, policy, started), timeout=policy.total_timeout
            )
        except asyncio.TimeoutError as exc:
            raise NetPolicyError("timeout", "total deadline exceeded", url) from exc

    async def _fetch(self, url, method, extra_headers, policy, started) -> FetchResult:
        target = parse_target(url, policy)
        visited = [target.url]
        redirects: list[str] = []
        while True:
            backend = _GuardedBackend(policy, self._resolver, self._inner_backend or httpcore.AnyIOBackend())
            request_headers = [(b"host", target.host_header.encode("ascii")),
                               (b"user-agent", policy.user_agent.encode("latin-1", "replace")),
                               (b"accept-encoding", b"identity"),
                               (b"connection", b"close")]
            for name, value in extra_headers.items():
                lowered = name.lower()
                if lowered in ("host", "connection", "accept-encoding", "content-length", "transfer-encoding"):
                    continue
                request_headers = [h for h in request_headers if h[0] != lowered.encode("ascii")]
                request_headers.append((lowered.encode("ascii"), str(value).encode("latin-1", "replace")))
            timeouts = {"connect": policy.connect_timeout, "read": policy.read_timeout,
                        "write": policy.read_timeout, "pool": policy.connect_timeout}
            try:
                async with httpcore.AsyncConnectionPool(network_backend=backend, max_connections=1, retries=0) as pool:
                    async with pool.stream(
                        method, target.url, headers=request_headers, extensions={"timeout": timeouts}
                    ) as response:
                        status = response.status
                        headers = {}
                        for raw_name, raw_value in response.headers:
                            name = raw_name.decode("latin-1").lower()
                            value = raw_value.decode("latin-1")
                            headers[name] = f"{headers[name]}, {value}" if name in headers else value
                        if status in _REDIRECT_STATUSES and "location" in headers:
                            body = b""
                        elif method == "HEAD":
                            body = b""
                        else:
                            body = await _read_limited(response, headers, policy.max_body_bytes, target.url)
            except NetPolicyError:
                raise
            except Exception as exc:  # ошибки httpcore, ssl, сокетов
                raise _map_transport_error(exc, target.url) from exc

            if status in _REDIRECT_STATUSES and "location" in headers:
                if len(redirects) >= policy.max_redirects:
                    raise NetPolicyError("redirect_limit", f"more than {policy.max_redirects}", target.url)
                try:
                    next_url = urljoin(target.url, headers["location"].strip())
                except ValueError as exc:
                    raise NetPolicyError("redirect_invalid", "bad Location", target.url) from exc
                target = parse_target(next_url, policy)
                if target.url in visited:
                    raise NetPolicyError("redirect_loop", target.url, target.url)
                visited.append(target.url)
                redirects.append(target.url)
                continue

            return FetchResult(
                url=url,
                final_url=target.url,
                status=status,
                headers=headers,
                body=body,
                redirects=redirects,
                peer_ip=backend.peer_ip,
                elapsed=time.monotonic() - started,
            )
