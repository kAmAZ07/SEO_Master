"""Проверка sitemap.xml: без внешних сущностей, с лимитами на размер, число записей и вложенность."""
from __future__ import annotations

import zlib
from urllib.parse import urlparse

from defusedxml import DefusedXmlException
from defusedxml import ElementTree as SafeET
from shared.netpolicy import NetPolicyError

from services.audit_service.netio import AuditFetcher, BudgetExhausted
from services.audit_service.public_policy import (
    PUBLIC_MAX_SITEMAP_FETCHES,
    SITEMAP_MAX_BYTES,
    SITEMAP_MAX_DEPTH,
    SITEMAP_MAX_ENTRIES,
)

_GZIP_MAGIC = b"\x1f\x8b"


def _sitemap_url(root_url: str) -> str:
    parsed = urlparse(root_url)
    return f"{parsed.scheme}://{parsed.netloc}/sitemap.xml"


class SitemapError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _decompress_limited(body: bytes, limit: int) -> bytes:
    """Распаковывает gzip не больше `limit` байт; превышение — признак распаковочной бомбы."""
    decoder = zlib.decompressobj(wbits=31)
    try:
        data = decoder.decompress(body, limit + 1)
    except zlib.error as exc:
        raise SitemapError("sitemap_corrupt") from exc
    if len(data) > limit or decoder.unconsumed_tail:
        raise SitemapError("sitemap_too_large")
    return data


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def parse_sitemap(body: bytes) -> tuple[str, list[str]]:
    """Возвращает ('urlset' | 'index', список loc). Небезопасный или битый XML — SitemapError."""
    if body[:2] == _GZIP_MAGIC:
        body = _decompress_limited(body, SITEMAP_MAX_BYTES)
    if len(body) > SITEMAP_MAX_BYTES:
        raise SitemapError("sitemap_too_large")
    try:
        root = SafeET.fromstring(body, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    except DefusedXmlException as exc:
        raise SitemapError("sitemap_unsafe_xml") from exc
    except SafeET.ParseError as exc:
        raise SitemapError("sitemap_invalid_xml") from exc

    kind = _local(root.tag)
    if kind not in ("urlset", "sitemapindex"):
        raise SitemapError("sitemap_invalid_xml")
    locs: list[str] = []
    for child in root:
        for node in child:
            if _local(node.tag) == "loc" and (node.text or "").strip():
                locs.append(node.text.strip())
                break
        if len(locs) >= SITEMAP_MAX_ENTRIES + 1:
            break
    return ("index" if kind == "sitemapindex" else "urlset"), locs


async def check_sitemap(root_url: str, fetcher: AuditFetcher | None = None, extra_urls: list[str] | None = None) -> dict:
    """Проверяет sitemap.xml (и sitemap из robots.txt, если /sitemap.xml нет).

    state: ok | missing | invalid | unreachable. Записи считаются, но не возвращаются целиком.
    """
    fetcher = fetcher or AuditFetcher.create()
    primary = _sitemap_url(root_url)
    result = {
        "sitemap_url": primary,
        "available": False,
        "state": "missing",
        "status_code": None,
        "content_type": None,
        "url_count": 0,
        "sitemaps_fetched": 0,
        "truncated": False,
        "error": None,
    }
    queue: list[tuple[str, int]] = [(primary, 0)] + [(u, 0) for u in (extra_urls or [])[:3]]
    seen: set[str] = set()
    fetched = 0
    host = urlparse(root_url).netloc.lower()

    while queue:
        url, depth = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        if fetched >= PUBLIC_MAX_SITEMAP_FETCHES:
            result["truncated"] = True
            break
        if urlparse(url).netloc.lower() != host and url != primary:
            continue  # чужой хост не расширяет область проверки
        try:
            fetched += 1
            response = await fetcher.fetch(url, kind="aux", max_body_bytes=SITEMAP_MAX_BYTES)
        except BudgetExhausted:
            result["truncated"] = True
            break
        except NetPolicyError as exc:
            if url == primary and result["state"] == "missing":
                result.update(state="unreachable", error=exc.code)
            continue

        if url == primary:
            result["status_code"] = response.status
            result["content_type"] = response.headers.get("content-type")
        if response.status >= 400:
            continue
        try:
            kind, locs = parse_sitemap(response.body)
        except SitemapError as exc:
            if result["state"] != "ok":
                result.update(state="invalid", error=exc.code)
            continue

        result.update(available=True, state="ok", error=None)
        if kind == "urlset":
            result["url_count"] += min(len(locs), SITEMAP_MAX_ENTRIES)
            if len(locs) > SITEMAP_MAX_ENTRIES or result["url_count"] > SITEMAP_MAX_ENTRIES:
                result["url_count"] = SITEMAP_MAX_ENTRIES
                result["truncated"] = True
                break
        elif depth + 1 >= SITEMAP_MAX_DEPTH:
            result["truncated"] = True
        else:
            queue.extend((loc, depth + 1) for loc in locs)

    result["sitemaps_fetched"] = fetched
    return result
