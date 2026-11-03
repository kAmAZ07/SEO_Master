"""Область проекта: точный origin и префикс пути (ТЗ REQ-SECURITY §5.2).

Модуль чистый: без сети, БД и часов. Область задаёт, какие адреса относятся к проекту:
совпадать должны схема, домен (IDN приводится к punycode), порт и префикс пути по границе сегмента.
`www.example.com` и `example.com` — разные области: поддомены не объединяются.

Неоднозначные пути (закодированный слеш, обратный слеш, управляющие символы, пустые
сегменты, `..` выше корня) в область не принимаются, а адрес с такой записью областью не
считается «внутри». Это закрывает обход префикса через `/blog/../admin`, `/blog%2f..%2fadmin`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote, unquote, urlsplit

from shared.netpolicy import NetPolicy, NetPolicyError, parse_target

MAX_PATH_PREFIX_LENGTH = 1024
_SEGMENT_SAFE = "-._~"
_DEFAULT_POLICY = NetPolicy()


class ScopeError(ValueError):
    """Адрес не может быть областью проекта. `code` стабилен и годится для ответа API."""

    def __init__(self, code: str, detail: str = ""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


@dataclass(frozen=True)
class ProjectScope:
    scheme: str
    host: str
    port: int
    path_prefix: str = "/"  # "/" или "/a/b" без завершающего слеша, сегменты в канонической кодировке

    @property
    def origin(self) -> str:
        default = 443 if self.scheme == "https" else 80
        host = f"[{self.host}]" if ":" in self.host else self.host
        netloc = host if self.port == default else f"{host}:{self.port}"
        return f"{self.scheme}://{netloc}"

    @property
    def is_origin_wide(self) -> bool:
        return self.path_prefix == "/"

    def contains(self, url: str) -> bool:
        """Принадлежит ли адрес области. Любая неоднозначность — «нет»."""
        try:
            other = scope_from_url(url)
        except ScopeError:
            return False
        if (other.scheme, other.host, other.port) != (self.scheme, self.host, self.port):
            return False
        return _prefix_contains(self.path_prefix, other.path_prefix)

    def challenge_url(self, filename: str) -> str:
        prefix = "" if self.is_origin_wide else self.path_prefix
        return f"{self.origin}{prefix}/{filename}"


def _canonical_segments(raw_path: str) -> list[str]:
    """Разбирает путь в список декодированных сегментов, разрешая `.` и `..`.

    Поднимает ScopeError на любой неоднозначной записи.
    """
    if len(raw_path) > MAX_PATH_PREFIX_LENGTH:
        raise ScopeError("path_too_long")
    # Путь абсолютного адреса всегда начинается с "/" (его гарантирует разбор адреса),
    # поэтому первый элемент пустой; завершающий "/" даёт пустой хвост.
    raw_segments = raw_path.split("/")[1:]
    if raw_segments and raw_segments[-1] == "":
        raw_segments = raw_segments[:-1]
    resolved: list[str] = []
    for raw in raw_segments:
        if raw == "":
            raise ScopeError("path_ambiguous", "empty segment")
        segment = unquote(raw, errors="strict")
        if "/" in segment or "\\" in segment or any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in segment):
            raise ScopeError("path_ambiguous", "encoded separator or control character")
        if segment == ".":
            continue
        if segment == "..":
            if not resolved:
                raise ScopeError("path_ambiguous", "traversal above root")
            resolved.pop()
            continue
        resolved.append(segment)
    return resolved


def _join(segments: list[str]) -> str:
    return "/" + "/".join(quote(segment, safe=_SEGMENT_SAFE) for segment in segments) if segments else "/"


def scope_from_url(url: str) -> ProjectScope:
    """Область из адреса. Query и fragment отбрасываются: они не часть области."""
    try:
        target = parse_target(url, _DEFAULT_POLICY)
    except NetPolicyError as exc:
        raise ScopeError(exc.code, str(exc)) from exc
    # Путь берём из исходной строки: parse_target склеивает путь с query.
    try:
        raw_path = urlsplit(url).path or "/"
        segments = _canonical_segments(raw_path)
    except UnicodeDecodeError as exc:
        raise ScopeError("path_ambiguous", "bad percent-encoding") from exc
    return ProjectScope(scheme=target.scheme, host=target.host, port=target.port, path_prefix=_join(segments))


def _prefix_contains(prefix: str, path: str) -> bool:
    if prefix == "/":
        return True
    return path == prefix or path.startswith(prefix + "/")


def scopes_overlap(a: ProjectScope, b: ProjectScope) -> bool:
    """Области пересекаются, если один префикс содержит другой на том же origin."""
    if (a.scheme, a.host, a.port) != (b.scheme, b.host, b.port):
        return False
    return _prefix_contains(a.path_prefix, b.path_prefix) or _prefix_contains(b.path_prefix, a.path_prefix)


def parse_scope(url: Optional[str]) -> ProjectScope:
    if not isinstance(url, str) or not url.strip():
        raise ScopeError("invalid_url", "empty")
    return scope_from_url(url.strip())
