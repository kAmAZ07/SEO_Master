"""Политика исходящих запросов `shared.netpolicy` (ТЗ §5.3, AC-02).

Главный принцип проверок: отказ подтверждается не кодом ответа, а тем, что к
сервису-ловушке не пришло ни одного соединения. Тесты не требуют внешней сети.
"""
import asyncio
import datetime
import gzip
import ipaddress
import os
import socket
import ssl
import tempfile
import zlib

import httpcore
import pytest

from shared.netpolicy import (
    NetPolicy,
    NetPolicyError,
    SafeHttpClient,
    is_public_ip,
    parse_target,
    resolve_public,
)

POLICY = NetPolicy()


def run(coro):
    return asyncio.run(coro)


def fetch_error(client, url, **kwargs):
    with pytest.raises(NetPolicyError) as exc:
        run(client.fetch(url, **kwargs))
    return exc.value


# --- публичные и непубличные адреса -----------------------------------------

@pytest.mark.parametrize("address", ["8.8.8.8", "1.1.1.1", "93.184.216.34", "2001:4860:4860::8888"])
def test_public_addresses_are_allowed(address):
    assert is_public_ip(address)


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1", "127.1.2.3", "10.0.0.1", "172.16.0.1", "172.31.255.254", "192.168.1.1",
        "169.254.169.254", "169.254.0.1", "100.64.0.1", "0.0.0.0", "224.0.0.1", "240.0.0.1",
        "255.255.255.255", "192.0.2.1", "198.51.100.7", "203.0.113.9", "198.18.0.1",
        "::1", "::", "fe80::1", "fc00::1", "fd00:ec2::254", "ff02::1", "2001:db8::1",
        "::ffff:127.0.0.1", "::ffff:10.0.0.1", "::ffff:169.254.169.254",
        "64:ff9b::7f00:1", "2002:7f00:1::1", "::7f00:1", "::a00:1",
        "fe80::1%eth0",
    ],
)
def test_non_public_addresses_are_blocked(address):
    assert not is_public_ip(address)


@pytest.mark.parametrize("garbage", ["", "abc", "999.1.1.1", "1.2.3", "8.8.8.8/32", None])
def test_malformed_address_is_not_public(garbage):
    assert not is_public_ip(garbage or "")


# --- разбор адреса -----------------------------------------------------------

def test_valid_urls_are_normalized():
    assert parse_target("HTTP://Example.COM./Path?Q=1", POLICY).url == "http://example.com/Path?Q=1"
    assert parse_target("https://example.com:443/", POLICY).url == "https://example.com/"
    assert parse_target("http://пример.рф/", POLICY).host == "xn--e1afmkfd.xn--p1ai"
    assert parse_target("https://example.com", POLICY).path_and_query == "/"
    # регистр пути и параметров не меняется
    assert parse_target("https://example.com/A/b?X=Y", POLICY).path_and_query == "/A/b?X=Y"


@pytest.mark.parametrize(
    "url,code",
    [
        ("ftp://example.com/", "scheme_not_allowed"),
        ("file:///etc/passwd", "scheme_not_allowed"),
        ("gopher://example.com/", "scheme_not_allowed"),
        ("javascript:alert(1)", "scheme_not_allowed"),
        ("//example.com/", "scheme_not_allowed"),
        ("example.com/path", "scheme_not_allowed"),
        ("http://user:pw@example.com/", "userinfo_not_allowed"),
        ("http://user@example.com/", "userinfo_not_allowed"),
        ("http://example.com\\@evil.com/", "invalid_url"),
        ("http://exa mple.com/", "invalid_url"),
        ("http://example.com/\r\nHost: evil", "invalid_url"),
        ("http://example.com/\x00", "invalid_url"),
        ("http://", "invalid_url"),
        ("http://example.com:22/", "port_not_allowed"),
        ("http://example.com:8080/", "port_not_allowed"),
        ("http://example.com:99999/", "invalid_url"),
        ("http://example.com:abc/", "invalid_url"),
        ("http://2130706433/", "host_blocked"),
        ("http://0x7f.1/", "host_blocked"),
        ("http://127.1/", "host_blocked"),
        ("http://017700000001/", "host_blocked"),
        ("http://localhost/", "host_blocked"),
        ("http://LOCALHOST./", "host_blocked"),
        ("http://foo.localhost/", "host_blocked"),
        ("http://printer.local/", "host_blocked"),
        ("http://db.internal/", "host_blocked"),
        ("http://-bad-.example/", "invalid_url"),
        ("http://exa_mple$.com/", "invalid_url"),
        ("http://" + "a" * 3000 + ".com/", "invalid_url"),
        ("", "invalid_url"),
    ],
)
def test_rejected_urls(url, code):
    with pytest.raises(NetPolicyError) as exc:
        parse_target(url, POLICY)
    assert exc.value.code == code
    assert exc.value.is_policy_block


def test_non_string_url_is_rejected():
    for value in (None, 123, b"http://example.com/"):
        with pytest.raises(NetPolicyError):
            parse_target(value, POLICY)


# --- разрешение имён ----------------------------------------------------------

def _resolver(mapping, calls=None):
    async def resolve(host, port):
        if calls is not None:
            calls.append(host)
        value = mapping[host]
        if isinstance(value, Exception):
            raise value
        return list(value)
    return resolve


def test_mixed_dns_answer_is_rejected_entirely():
    resolver = _resolver({"mixed.example": ["93.184.216.34", "127.0.0.1"]})
    with pytest.raises(NetPolicyError) as exc:
        run(resolve_public("mixed.example", 80, POLICY, resolver))
    assert exc.value.code == "address_blocked"


@pytest.mark.parametrize(
    "answer",
    [socket.gaierror("no such host"), OSError("boom"), RuntimeError("resolver crashed"), KeyError("x"), [], ["not-an-ip"]],
)
def test_dns_failure_or_garbage_means_refusal_not_permission(answer):
    resolver = _resolver({"bad.example": answer})
    with pytest.raises(NetPolicyError) as exc:
        run(resolve_public("bad.example", 80, POLICY, resolver))
    assert exc.value.code in ("dns_failure", "address_blocked")


def test_ip_literal_does_not_use_resolver():
    calls = []
    resolver = _resolver({}, calls)
    assert run(resolve_public("93.184.216.34", 80, POLICY, resolver)) == ["93.184.216.34"]
    assert calls == []
    with pytest.raises(NetPolicyError):
        run(resolve_public("10.1.2.3", 80, POLICY, resolver))
    assert calls == []


def test_private_allowlist_requires_test_profile(monkeypatch):
    monkeypatch.delenv("NETPOLICY_PROFILE", raising=False)
    with pytest.raises(ValueError):
        NetPolicy(allow_private_hosts=frozenset({"fixture.test"}))
    monkeypatch.setenv("NETPOLICY_PROFILE", "production")
    with pytest.raises(ValueError):
        NetPolicy(allow_private_hosts=frozenset({"fixture.test"}))
    monkeypatch.setenv("NETPOLICY_PROFILE", "test")
    assert NetPolicy(allow_private_hosts=frozenset({"fixture.test"})).allow_private_hosts == {"fixture.test"}


# --- клиент на подставном сетевом бэкенде (без реальных соединений) -----------

class FakeStream(httpcore.AsyncNetworkStream):
    def __init__(self, payload: bytes, stall: bool = False):
        self._payload = payload
        self._stall = stall
        self.written = bytearray()

    async def read(self, max_bytes, timeout=None):
        if self._stall:
            await asyncio.sleep(3600)
        chunk, self._payload = self._payload[:max_bytes], self._payload[max_bytes:]
        return chunk

    async def write(self, buffer, timeout=None):
        self.written += buffer

    async def aclose(self):
        pass

    def get_extra_info(self, info):
        return None


class FakeBackend(httpcore.AsyncNetworkBackend):
    """Отвечает заранее заготовленными ответами по порядку соединений."""

    def __init__(self, responses, stall=False):
        self.responses = list(responses)
        self.connects = []
        self.streams = []
        self.stall = stall

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        self.connects.append((host, port))
        stream = FakeStream(self.responses.pop(0), stall=self.stall)
        self.streams.append(stream)
        return stream

    async def connect_unix_socket(self, path, timeout=None, socket_options=None):
        raise AssertionError("unix sockets must never be used")

    async def sleep(self, seconds):
        await asyncio.sleep(seconds)


def http_response(body=b"ok", status=200, headers=None, raw_body=None):
    lines = [f"HTTP/1.1 {status} X", f"Content-Length: {len(body)}"]
    for key, value in (headers or {}).items():
        lines.append(f"{key}: {value}")
    return ("\r\n".join(lines) + "\r\n\r\n").encode() + (body if raw_body is None else raw_body)


def redirect(location, status=302):
    return http_response(b"", status=status, headers={"Location": location})


PUBLIC = {"example.com": ["93.184.216.34"], "other.example": ["8.8.4.4"], "internal.example": ["10.0.0.5"]}


def client_with(responses, mapping=None, policy=None, calls=None, stall=False):
    backend = FakeBackend(responses, stall=stall)
    client = SafeHttpClient(policy or POLICY, resolver=_resolver(mapping or PUBLIC, calls), network_backend=backend)
    return client, backend


def test_fetch_connects_to_validated_ip_and_keeps_host_name():
    client, backend = client_with([http_response(b"hello")])
    result = run(client.fetch("http://Example.com/page?x=1"))
    assert result.status == 200 and result.body == b"hello"
    assert backend.connects == [("93.184.216.34", 80)]
    assert result.peer_ip == "93.184.216.34"
    sent = bytes(backend.streams[0].written)
    assert sent.startswith(b"GET /page?x=1 HTTP/1.1\r\n")
    assert b"host: example.com\r\n" in sent.lower()
    assert b"accept-encoding: identity" in sent.lower()


def test_dns_is_resolved_once_per_connection_so_rebinding_cannot_swap_the_address():
    answers = iter([["93.184.216.34"], ["127.0.0.1"], ["127.0.0.1"]])
    calls = []

    async def rebinding_resolver(host, port):
        calls.append(host)
        return next(answers)

    backend = FakeBackend([http_response(b"x")])
    client = SafeHttpClient(POLICY, resolver=rebinding_resolver, network_backend=backend)
    run(client.fetch("http://evil.example/"))
    assert calls == ["evil.example"]
    assert backend.connects == [("93.184.216.34", 80)]


def test_redirect_to_internal_name_is_blocked_before_any_connection_to_it():
    client, backend = client_with([redirect("http://internal.example/admin")])
    error = fetch_error(client, "http://example.com/")
    assert error.code == "address_blocked"
    assert backend.connects == [("93.184.216.34", 80)]  # к 10.0.0.5 соединения не было


@pytest.mark.parametrize(
    "location,code",
    [
        ("http://169.254.169.254/latest/meta-data/", "address_blocked"),
        ("http://127.0.0.1/", "address_blocked"),
        ("http://[::1]/", "address_blocked"),
        ("http://[::ffff:10.0.0.1]/", "address_blocked"),
        ("file:///etc/passwd", "scheme_not_allowed"),
        ("gopher://example.com/", "scheme_not_allowed"),
        ("http://example.com:22/", "port_not_allowed"),
        ("http://user:pw@example.com/", "userinfo_not_allowed"),
        ("http://2130706433/", "host_blocked"),
        ("http://localhost/", "host_blocked"),
        ("http://example.com/\\@evil", "invalid_url"),
    ],
)
def test_dangerous_redirect_targets_are_blocked(location, code):
    client, backend = client_with([redirect(location)])
    assert fetch_error(client, "http://example.com/").code == code
    assert len(backend.connects) == 1


def test_relative_redirect_is_followed_and_recorded():
    client, backend = client_with([redirect("/next"), http_response(b"final")])
    result = run(client.fetch("http://example.com/start"))
    assert result.body == b"final"
    assert result.redirects == ["http://example.com/next"]
    assert result.final_url == "http://example.com/next"


def test_redirect_loop_and_chain_limit():
    client, _ = client_with([redirect("/a"), redirect("/a")])
    assert fetch_error(client, "http://example.com/a").code == "redirect_loop"

    chain = [redirect(f"/hop{i}") for i in range(1, 12)]
    client, backend = client_with(chain)
    error = fetch_error(client, "http://example.com/")
    assert error.code == "redirect_limit"
    assert len(backend.connects) == POLICY.max_redirects + 1


def test_redirect_without_location_is_returned_as_final_response():
    client, _ = client_with([http_response(b"", status=302)])
    assert run(client.fetch("http://example.com/")).status == 302


def test_response_size_limit_by_content_length_and_by_stream():
    small = NetPolicy(max_body_bytes=1000)
    client, _ = client_with([http_response(b"a" * 5000)], policy=small)
    assert fetch_error(client, "http://example.com/").code == "response_too_large"

    streamed = b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n" + b"".join(
        b"400\r\n" + b"b" * 1024 + b"\r\n" for _ in range(10)
    ) + b"0\r\n\r\n"
    client, _ = client_with([streamed], policy=small)
    assert fetch_error(client, "http://example.com/").code == "response_too_large"

    client, _ = client_with([http_response(b"a" * 1000)], policy=small)
    assert len(run(client.fetch("http://example.com/")).body) == 1000


def test_decompression_bomb_is_stopped_at_the_limit():
    bomb = gzip.compress(b"\0" * (50 * 1024 * 1024))
    assert len(bomb) < 100 * 1024
    policy = NetPolicy(max_body_bytes=1024 * 1024)
    client, _ = client_with([http_response(b"", headers={"Content-Encoding": "gzip"}, raw_body=bomb).replace(
        b"Content-Length: 0", f"Content-Length: {len(bomb)}".encode())], policy=policy)
    assert fetch_error(client, "http://example.com/").code == "response_too_large"


def test_small_gzip_body_is_decoded_and_deflate_too():
    for payload, header in ((gzip.compress(b"hello gzip"), "gzip"), (zlib.compress(b"hello zlib"), "deflate")):
        raw = http_response(b"", headers={"Content-Encoding": header}, raw_body=payload).replace(
            b"Content-Length: 0", f"Content-Length: {len(payload)}".encode())
        client, _ = client_with([raw])
        assert run(client.fetch("http://example.com/")).body.startswith(b"hello")


def test_unsupported_or_broken_encoding_is_an_error():
    client, _ = client_with([http_response(b"x", headers={"Content-Encoding": "br"})])
    assert fetch_error(client, "http://example.com/").code == "unsupported_encoding"
    client, _ = client_with([http_response(b"not gzip at all", headers={"Content-Encoding": "gzip"})])
    assert fetch_error(client, "http://example.com/").code == "protocol_error"


def test_only_get_and_head_are_allowed_and_head_has_no_body():
    client, _ = client_with([])
    for method in ("POST", "PUT", "DELETE", "CONNECT"):
        assert fetch_error(client, "http://example.com/", method=method).code == "method_not_allowed"
    client, _ = client_with([http_response(b"", headers={"X-Test": "1"})])
    result = run(client.fetch("http://example.com/", method="head"))
    assert result.body == b"" and result.headers["x-test"] == "1"


def test_caller_cannot_override_host_or_connection_headers():
    client, backend = client_with([http_response(b"x")])
    run(client.fetch("http://example.com/", headers={"Host": "evil.example", "Accept-Encoding": "br", "X-Custom": "1"}))
    sent = bytes(backend.streams[0].written).lower()
    assert b"host: example.com\r\n" in sent and b"evil.example" not in sent
    assert b"accept-encoding: identity" in sent and b"x-custom: 1" in sent


def test_total_deadline_stops_a_stalled_server():
    client, _ = client_with([b""], policy=NetPolicy(total_timeout=0.3), stall=True)
    assert fetch_error(client, "http://example.com/").code == "timeout"


def test_error_attributes_distinguish_policy_blocks_from_transport_errors():
    blocked = NetPolicyError("address_blocked")
    transport = NetPolicyError("timeout")
    assert blocked.is_policy_block and not blocked.retryable
    assert not transport.is_policy_block and transport.retryable


# --- реальные сокеты: сервис-ловушка ------------------------------------------

class Server:
    """Локальный сервер на 127.0.0.1: считает принятые соединения и отдаёт заданный ответ."""

    def __init__(self, handler=None, ssl_context=None):
        self.connections = 0
        self._handler = handler
        self._ssl = ssl_context
        self._server = None
        self.port = 0

    async def __aenter__(self):
        async def on_connect(reader, writer):
            self.connections += 1
            try:
                await reader.readuntil(b"\r\n\r\n")
                if self._handler is None:
                    writer.write(http_response(b"trap"))
                else:
                    await self._handler(reader, writer)
                await writer.drain()
            except Exception:
                pass
            finally:
                writer.close()

        self._server = await asyncio.start_server(on_connect, "127.0.0.1", 0, ssl=self._ssl)
        self.port = self._server.sockets[0].getsockname()[1]
        return self

    async def __aexit__(self, *exc):
        self._server.close()
        await self._server.wait_closed()


def with_port(port, **kwargs):
    return NetPolicy(allowed_ports=frozenset({80, 443, port}), **kwargs)


def test_trap_service_never_receives_a_connection_for_blocked_targets():
    async def scenario():
        async with Server() as trap:
            resolver = _resolver({
                "evil.test": ["127.0.0.1"],
                "mixed.test": ["93.184.216.34", "127.0.0.1"],
                "v6.test": ["::1"],
                "mapped.test": ["::ffff:127.0.0.1"],
                "dns-failure.test": socket.gaierror("no such host"),
            })
            client = SafeHttpClient(with_port(trap.port), resolver=resolver)
            p = trap.port
            urls = [
                f"http://127.0.0.1:{p}/", f"http://[::1]:{p}/", f"http://[::ffff:127.0.0.1]:{p}/",
                f"http://localhost:{p}/", f"http://2130706433:{p}/", f"http://0x7f.1:{p}/",
                f"http://evil.test:{p}/", f"http://mixed.test:{p}/", f"http://v6.test:{p}/",
                f"http://mapped.test:{p}/", f"http://user:pw@127.0.0.1:{p}/",
                f"http://dns-failure.test:{p}/",
            ]
            codes = []
            for url in urls:
                try:
                    await client.fetch(url)
                    codes.append("fetched")
                except NetPolicyError as exc:
                    codes.append(exc.code)
            return trap.connections, codes

    connections, codes = run(scenario())
    assert connections == 0
    assert "fetched" not in codes
    assert all(code in {"address_blocked", "host_blocked", "userinfo_not_allowed", "dns_failure"} for code in codes)


def test_redirect_from_allowed_fixture_to_trap_is_blocked():
    async def scenario(monkeypatch):
        async with Server() as trap:
            async def redirecting(reader, writer):
                writer.write(redirect(f"http://127.0.0.1:{trap.port}/secret"))
            async with Server(redirecting) as fixture:
                monkeypatch.setenv("NETPOLICY_PROFILE", "test")
                policy = with_port(fixture.port, allow_private_hosts=frozenset({"allowed.test"}))
                policy = NetPolicy(allowed_ports=frozenset({fixture.port, trap.port}),
                                   allow_private_hosts=frozenset({"allowed.test"}))
                client = SafeHttpClient(policy, resolver=_resolver({"allowed.test": ["127.0.0.1"]}))
                with pytest.raises(NetPolicyError) as exc:
                    await client.fetch(f"http://allowed.test:{fixture.port}/")
                return exc.value.code, fixture.connections, trap.connections

    code, fixture_hits, trap_hits = run(scenario(pytest.MonkeyPatch()))
    assert code == "address_blocked"
    assert fixture_hits == 1 and trap_hits == 0


def test_allowed_fixture_host_works_over_real_socket(monkeypatch):
    monkeypatch.setenv("NETPOLICY_PROFILE", "test")

    async def scenario():
        async with Server() as fixture:
            policy = NetPolicy(allowed_ports=frozenset({fixture.port}), allow_private_hosts=frozenset({"allowed.test"}))
            client = SafeHttpClient(policy, resolver=_resolver({"allowed.test": ["127.0.0.1"]}))
            return await client.fetch(f"http://allowed.test:{fixture.port}/x")

    result = run(scenario())
    assert result.status == 200 and result.body == b"trap" and result.peer_ip == "127.0.0.1"


def test_read_timeout_on_silent_server(monkeypatch):
    monkeypatch.setenv("NETPOLICY_PROFILE", "test")

    async def silent(reader, writer):
        await asyncio.sleep(5)

    async def scenario():
        async with Server(silent) as fixture:
            policy = NetPolicy(allowed_ports=frozenset({fixture.port}), allow_private_hosts=frozenset({"allowed.test"}),
                               read_timeout=0.3, total_timeout=5)
            client = SafeHttpClient(policy, resolver=_resolver({"allowed.test": ["127.0.0.1"]}))
            started = asyncio.get_running_loop().time()
            with pytest.raises(NetPolicyError) as exc:
                await client.fetch(f"http://allowed.test:{fixture.port}/")
            return exc.value.code, asyncio.get_running_loop().time() - started

    code, elapsed = run(scenario())
    assert code == "timeout" and elapsed < 3


def test_connection_refused_is_a_transport_error(monkeypatch):
    monkeypatch.setenv("NETPOLICY_PROFILE", "test")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        closed_port = s.getsockname()[1]
    policy = NetPolicy(allowed_ports=frozenset({closed_port}), allow_private_hosts=frozenset({"allowed.test"}))
    client = SafeHttpClient(policy, resolver=_resolver({"allowed.test": ["127.0.0.1"]}))
    error = fetch_error(client, f"http://allowed.test:{closed_port}/")
    assert error.code == "connect_error" and not error.is_policy_block and error.retryable


def _self_signed_context():
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "allowed.test")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("allowed.test")]), critical=False)
        .sign(key, hashes.SHA256())
    )
    directory = tempfile.mkdtemp()
    cert_path, key_path = os.path.join(directory, "c.pem"), os.path.join(directory, "k.pem")
    with open(cert_path, "wb") as fh:
        fh.write(cert.public_bytes(serialization.Encoding.PEM))
    with open(key_path, "wb") as fh:
        fh.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                   serialization.NoEncryption()))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)
    return context


def test_untrusted_tls_certificate_is_rejected(monkeypatch):
    monkeypatch.setenv("NETPOLICY_PROFILE", "test")

    async def scenario():
        async with Server(ssl_context=_self_signed_context()) as fixture:
            policy = NetPolicy(allowed_ports=frozenset({fixture.port}), allow_private_hosts=frozenset({"allowed.test"}))
            client = SafeHttpClient(policy, resolver=_resolver({"allowed.test": ["127.0.0.1"]}))
            with pytest.raises(NetPolicyError) as exc:
                await client.fetch(f"https://allowed.test:{fixture.port}/")
            return exc.value.code

    assert run(scenario()) == "tls_error"
