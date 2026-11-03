"""Фикстура публичного аудита: версионируемый сайт из 10 страниц с известными проблемами и поддельный сетевой клиент.

Версия фикстуры меняется при любом изменении страниц; ожидаемые результаты в тестах привязаны к ней.
Клиент повторяет контракт `SafeHttpClient.fetch` (проверка URL, редиректы, лимит тела, коды ошибок)
и записывает каждый запрос, чтобы тесты могли доказать отсутствие обращений к запрещённым адресам.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

from shared.netpolicy import FetchResult, NetPolicy, NetPolicyError, is_public_ip, parse_target

FIXTURE_VERSION = "1"
HOST = "fixture.test"
BASE = f"https://{HOST}"
PUBLIC_IP = "93.184.216.34"
HTML = {"content-type": "text/html; charset=utf-8"}


@dataclass
class Resp:
    status: int = 200
    body: bytes | str = b""
    headers: dict = field(default_factory=lambda: dict(HTML))
    redirect: str | None = None
    delay: float = 0.0
    raises: str | None = None  # код NetPolicyError, который вернёт «сеть»
    head_status: int | None = None  # ответ на HEAD, если отличается

    def body_bytes(self) -> bytes:
        return self.body.encode() if isinstance(self.body, str) else self.body


def html_page(title="Страница сайта примера", description="Подробное описание страницы для поисковой выдачи, около шестидесяти символов.",
              h1s=("Основной заголовок",), canonical="", links=(), jsonld="", extra=""):
    head = []
    if title is not None:
        head.append(f"<title>{title}</title>")
    if description is not None:
        head.append(f'<meta name="description" content="{description}">')
    if canonical:
        head.append(f'<link rel="canonical" href="{canonical}">')
    body = "".join(f"<h1>{h}</h1>" for h in h1s)
    body += "<p>Текст страницы, достаточно длинный, чтобы не считаться пустой оболочкой SPA.</p>"
    body += "".join(f'<a href="{href}">ссылка</a>' for href in links)
    scripts = f'<script type="application/ld+json">{jsonld}</script>' if jsonld else ""
    return f"<!doctype html><html><head>{''.join(head)}{scripts}</head><body>{body}{extra}</body></html>"


_ORG = '{"@context":"https://schema.org","@type":"Organization","name":"Fixture"}'


def default_site() -> dict[str, Resp]:
    """10 доступных HTML-страниц до глубины 2 и известные проблемы (см. EXPECTED_CODES)."""
    def canon(path):
        return f"{BASE}{path}"

    site = {
        "/robots.txt": Resp(body="User-agent: *\nDisallow: /private\n", headers={"content-type": "text/plain"}),
        "/": Resp(body=html_page(canonical=canon("/"), jsonld=_ORG,
                                 links=["/about", "/products", "/blog", "/contact", "/team", "/faq", "/broken-server", "/private"])),
        "/about": Resp(body=html_page(title=None, canonical=canon("/about"), links=["/about/history"])),
        "/products": Resp(body=html_page(description=None, canonical=canon("/products"), links=["/products/widget"])),
        "/blog": Resp(body=html_page(h1s=(), canonical=canon("/blog"))),
        "/contact": Resp(body=html_page(h1s=("Контакты компании", "Как нас найти"), canonical=canon("/contact"))),
        "/team": Resp(body=html_page(canonical=canon("/team-old"))),
        "/faq": Resp(body=html_page(canonical=canon("/faq"), jsonld="{not json")),
        "/broken-server": Resp(status=500, body=html_page(canonical=canon("/broken-server"))),
        "/about/history": Resp(body=html_page(canonical=canon("/about/history"), links=["/gone", "/nohead"])),
        "/products/widget": Resp(body=html_page(canonical=canon("/products/widget"), links=["/deep/level3"])),
        "/deep/level3": Resp(body=html_page(canonical=canon("/deep/level3"))),
        "/nohead": Resp(body=html_page(canonical=canon("/nohead")), head_status=405),
        "/private": Resp(body=html_page(canonical=canon("/private"))),
    }
    return site


# Известные проблемы версии 1 фикстуры.
EXPECTED_CODES = {
    "sitemap_missing", "title_missing", "description_missing", "h1_missing", "h1_multiple",
    "canonical_mismatch", "jsonld_invalid_json", "page_server_error", "broken_link_404", "cwv_lcp_poor",
}
EXPECTED_TOP5 = ["cwv_lcp_poor", "page_server_error", "broken_link_404", "canonical_mismatch", "h1_missing"]
CRAWLED_PATHS = ["/", "/about", "/products", "/blog", "/contact", "/team", "/faq", "/broken-server",
                 "/about/history", "/products/widget"]


class FixtureClient:
    """Замена SafeHttpClient: тот же контракт, но без сети."""

    def __init__(self, site: dict[str, Resp] | None = None, dns: dict[str, list[str]] | None = None, host: str = HOST):
        self.site = site if site is not None else default_site()
        self.dns = dns if dns is not None else {host: [PUBLIC_IP]}
        self.requests: list[tuple[str, str]] = []

    async def _resolve(self, host: str, port: int) -> list[str]:
        if host not in self.dns:
            raise OSError("NXDOMAIN")
        return self.dns[host]

    @property
    def resolver(self):
        return self._resolve

    def paths(self, method: str | None = None) -> list[str]:
        return [urlsplit(u).path for m, u in self.requests if method in (None, m)]

    async def fetch(self, url, *, method="GET", headers=None, policy: NetPolicy | None = None) -> FetchResult:
        policy = policy or NetPolicy()
        current = parse_target(url, policy).url
        redirects: list[str] = []
        seen = {current}
        while True:
            target = parse_target(current, policy)
            addresses = await self._resolve_checked(target.host, target.port)
            self.requests.append((method, current))
            resp = self.site.get(urlsplit(current).path or "/")
            if resp is None:
                resp = Resp(status=404, body="not found")
            if resp.delay:
                if resp.delay > policy.total_timeout:
                    await asyncio.sleep(policy.total_timeout)
                    raise NetPolicyError("timeout", "total deadline exceeded", current)
                await asyncio.sleep(resp.delay)
            if resp.raises:
                raise NetPolicyError(resp.raises, "fixture", current)
            if resp.redirect:
                if len(redirects) >= policy.max_redirects:
                    raise NetPolicyError("redirect_limit", "", current)
                nxt = parse_target(urljoin(current, resp.redirect), policy).url
                if nxt in seen:
                    raise NetPolicyError("redirect_loop", "", current)
                seen.add(nxt)
                redirects.append(nxt)
                current = nxt
                continue
            body = b"" if method == "HEAD" else resp.body_bytes()
            if len(body) > policy.max_body_bytes:
                raise NetPolicyError("response_too_large", "", current)
            status = resp.head_status if (method == "HEAD" and resp.head_status) else resp.status
            return FetchResult(url=url, final_url=current, status=status, headers=dict(resp.headers), body=body,
                               redirects=redirects, peer_ip=addresses[0], elapsed=0.0)

    async def _resolve_checked(self, host: str, port: int) -> list[str]:
        try:
            addresses = await self._resolve(host, port)
        except Exception as exc:
            raise NetPolicyError("dns_failure", type(exc).__name__, host) from exc
        for address in addresses:
            if not is_public_ip(address):
                raise NetPolicyError("address_blocked", f"{host} -> {address}", host)
        return addresses
