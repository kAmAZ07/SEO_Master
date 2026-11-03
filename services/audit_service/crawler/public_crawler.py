"""Обход сайта для аудита.

Основной (не-JS) путь работает только через `AuditFetcher` / `SafeHttpClient`: адреса
проверяются при соединении, редиректы, размеры и время ограничены, бюджет задания общий.
Обход ограничен одним хостом (`www.` считается тем же узлом), robots.txt применяется
к каждому URL. JS-рендеринг (Playwright) доступен только полному аудиту, публичный его
не использует.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional
from urllib.parse import urldefrag, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup
from shared.netpolicy import NetPolicyError

from services.audit_service.config import settings
from services.audit_service.netio import AuditFetcher, BudgetExhausted
from services.audit_service.analyzers.robots_checker import RobotsInfo

ProgressCallback = Callable[..., Awaitable[None]]


@dataclass
class CrawledPage:
    url: str
    status_code: int | None
    final_url: str | None
    html: str | None
    title: str | None
    description: str | None
    h1: str | None
    links: list[str]
    error: str | None
    h1_count: int = 0


def _canonical_url(url: str) -> str | None:
    """Приводит URL к виду для дедупликации: схема и хост в нижнем регистре, без фрагмента и порта по умолчанию."""
    url, _ = urldefrag(url.strip())
    parts = urlsplit(url)
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        return None
    host = parts.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    port = parts.port
    default = 443 if parts.scheme.lower() == "https" else 80
    netloc = host if port in (None, default) else f"{host}:{port}"
    return urlunsplit((parts.scheme.lower(), netloc, parts.path or "/", parts.query, ""))


def _normalize_url(base: str, href: str) -> str | None:
    if not href:
        return None
    href = href.strip()
    lowered = href.lower()
    if lowered.startswith(("mailto:", "tel:", "javascript:", "data:", "#")):
        return None
    try:
        return _canonical_url(urljoin(base, href))
    except ValueError:
        return None


def _host_key(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _same_site(root: str, candidate: str) -> bool:
    """Тот же узел (без учёта `www.`). Совпадение зарегистрированного домена не даёт доступа к поддоменам."""
    return _host_key(root) == _host_key(candidate)


def _extract_basic(html: str) -> tuple[str | None, str | None, str | None, list[str], int]:
    soup = BeautifulSoup(html, "lxml")
    title = soup.title.text.strip() if soup.title and soup.title.text else None
    desc = None
    m = soup.find("meta", attrs={"name": re.compile(r"^description$", re.I)})
    if m and m.get("content"):
        desc = m["content"].strip()
    h1_nodes = [h for h in soup.find_all("h1") if h.get_text(strip=True)]
    h1 = h1_nodes[0].get_text(strip=True) if h1_nodes else None
    links = [a.get("href") for a in soup.find_all("a") if a.get("href")]
    return title, desc, h1, links, len(h1_nodes)


def _decode_body(body: bytes, content_type: str) -> str:
    match = re.search(r"charset=([\w-]+)", content_type or "", re.I)
    for encoding in ([match.group(1)] if match else []) + ["utf-8"]:
        try:
            return body.decode(encoding, errors="replace")
        except LookupError:
            continue
    return body.decode("utf-8", errors="replace")


def _is_html(content_type: str) -> bool:
    lowered = (content_type or "").lower()
    return not lowered or "html" in lowered


async def fetch_html_playwright(url: str, timeout_ms: int, user_agent: str) -> tuple[int | None, str | None, str | None, str | None]:
    """JS-рендеринг. Только для полного аудита; не используется в публичном режиме."""
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            context = await browser.new_context(user_agent=user_agent)
            try:
                page = await context.new_page()
                resp = await page.goto(url, wait_until="networkidle", timeout=timeout_ms)
                return (resp.status if resp else None), page.url, await page.content(), None
            except Exception as e:  # noqa: BLE001 - любая ошибка браузера превращается в код
                return None, None, None, type(e).__name__
            finally:
                await context.close()
        finally:
            await browser.close()


def _failed_page(url: str, error: str, status: int | None = None, final_url: str | None = None) -> CrawledPage:
    return CrawledPage(url=url, status_code=status, final_url=final_url, html=None, title=None,
                       description=None, h1=None, links=[], error=error)


async def crawl_public(
    root_url: str,
    max_pages: int = 10,
    max_depth: int = 2,
    js_render: bool = False,
    timeout_s: float = 10.0,
    respect_robots: bool = True,
    *,
    fetcher: Optional[AuditFetcher] = None,
    robots: Optional[RobotsInfo] = None,
    on_progress: Optional[ProgressCallback] = None,
) -> dict:
    fetcher = fetcher or AuditFetcher.create()
    root = _canonical_url(root_url) or root_url
    visited: set[str] = set()
    queue: list[tuple[str, int]] = [(root, 0)]
    pages: list[CrawledPage] = []

    counters = {"timeout": 0, "ssl": 0, "redirect_loop": 0, "server_error": 0, "blocked": 0, "non_html": 0}
    anti_bot_detected = False
    spa_detected = False
    max_depth_reached = 0
    truncated_reason: str | None = None

    while queue and len(pages) < max_pages:
        url, depth = queue.pop(0)
        if url in visited:
            continue
        visited.add(url)

        if respect_robots and robots is not None and not robots.can_fetch(url):
            counters["blocked"] += 1
            continue
        max_depth_reached = max(max_depth_reached, depth)

        status = final_url = html = None
        content_type = ""
        err: str | None = None
        try:
            if js_render:
                status, final_url, html, err = await fetch_html_playwright(
                    url, timeout_ms=int(timeout_s * 1000), user_agent=settings.user_agent
                )
            else:
                response = await fetcher.fetch(url, kind="page")
                status, final_url = response.status, response.final_url
                content_type = response.headers.get("content-type", "")
                if not _is_html(content_type):
                    counters["non_html"] += 1
                    if depth == 0:
                        pages.append(_failed_page(url, "not_html", status, final_url))
                    continue
                html = _decode_body(response.body, content_type)
        except BudgetExhausted as exc:
            truncated_reason = exc.reason
            break
        except NetPolicyError as exc:
            if exc.code == "timeout":
                counters["timeout"] += 1
                code = "timeout"
            elif exc.code == "tls_error":
                counters["ssl"] += 1
                code = "ssl_error"
            elif exc.code in ("redirect_loop", "redirect_limit"):
                counters["redirect_loop"] += 1
                code = "redirect_loop"
            elif exc.is_policy_block:
                counters["blocked"] += 1
                code = f"blocked_{exc.code}"
            else:
                code = exc.code
            pages.append(_failed_page(url, code))
            if on_progress:
                await on_progress("crawl", pages_done=len(pages), pages_max=max_pages)
            continue

        if err:
            pages.append(_failed_page(url, err, status, final_url))
            continue

        if status in (403, 429):
            anti_bot_detected = True
        if status and 500 <= status <= 599:
            counters["server_error"] += 1

        title = desc = h1 = None
        h1_count = 0
        all_links: list[str] = []
        if html:
            title, desc, h1, raw_links, h1_count = _extract_basic(html)
            if title is None and desc is None and h1 is None:
                body_text = BeautifulSoup(html, "lxml").get_text(" ", strip=True)
                if len(body_text) < 30:
                    spa_detected = True
            base = final_url or url
            seen_links: set[str] = set()
            for href in raw_links:
                normalized = _normalize_url(base, href)
                if normalized and normalized not in seen_links and _same_site(root, normalized):
                    seen_links.add(normalized)
                    all_links.append(normalized)

        pages.append(CrawledPage(url=url, status_code=status, final_url=final_url, html=html, title=title,
                                 description=desc, h1=h1, links=all_links, error=None, h1_count=h1_count))
        if depth < max_depth:
            for link in all_links:
                if link not in visited and len(visited) + len(queue) < 2 * max_pages:
                    queue.append((link, depth + 1))
        if on_progress:
            await on_progress("crawl", pages_done=len(pages), pages_max=max_pages)

    coverage = {
        "attempted": len(visited),
        "processed": len(pages),
        "max_pages": max_pages,
        "max_depth": max_depth,
        "max_depth_reached": max_depth_reached,
    }
    summary = {
        "coverage": coverage,
        "timeout_count": counters["timeout"],
        "ssl_errors_count": counters["ssl"],
        "redirect_loops_count": counters["redirect_loop"],
        "server_errors_count": counters["server_error"],
        "blocked_pages_count": counters["blocked"],
        "non_html_skipped": counters["non_html"],
        "anti_bot_detected": anti_bot_detected,
        "spa_detected": spa_detected,
        "js_render_used": js_render,
        "truncated_reason": truncated_reason,
    }
    return {"root_url": root_url, "pages": [p.__dict__ for p in pages], "summary": summary}
