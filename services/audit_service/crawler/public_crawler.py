import asyncio
import re
from dataclasses import dataclass
from urllib.parse import urljoin, urldefrag, urlparse

import httpx
from bs4 import BeautifulSoup
import tldextract
from playwright.async_api import async_playwright

from services.audit_service.config import settings


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


def _normalize_url(base: str, href: str) -> str | None:
    if not href:
        return None
    href = href.strip()
    if href.startswith("mailto:") or href.startswith("tel:") or href.startswith("javascript:"):
        return None
    u = urljoin(base, href)
    u, _ = urldefrag(u)
    p = urlparse(u)
    if p.scheme not in ("http", "https"):
        return None
    return u


def _same_site(root: str, candidate: str) -> bool:
    r = tldextract.extract(root)
    c = tldextract.extract(candidate)
    return (r.domain, r.suffix) == (c.domain, c.suffix)


def _extract_basic(html: str) -> tuple[str | None, str | None, str | None, list[str]]:
    soup = BeautifulSoup(html, "lxml")
    title = soup.title.text.strip() if soup.title and soup.title.text else None
    desc = None
    m = soup.find("meta", attrs={"name": re.compile(r"^description$", re.I)})
    if m and m.get("content"):
        desc = m["content"].strip()
    h1 = None
    h = soup.find("h1")
    if h and h.get_text(strip=True):
        h1 = h.get_text(strip=True)
    links = []
    for a in soup.find_all("a"):
        href = a.get("href")
        if href:
            links.append(href)
    return title, desc, h1, links


async def fetch_html_httpx(url: str, timeout_s: float, headers: dict[str, str]) -> tuple[int | None, str | None, str | None, str | None]:
    async with httpx.AsyncClient(follow_redirects=True, headers=headers, timeout=timeout_s) as client:
        r = await client.get(url)
        return r.status_code, str(r.url), r.text, None


async def fetch_html_playwright(url: str, timeout_ms: int, user_agent: str) -> tuple[int | None, str | None, str | None, str | None]:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(user_agent=user_agent)
        page = await context.new_page()
        try:
            resp = await page.goto(url, wait_until="networkidle", timeout=timeout_ms)
            html = await page.content()
            status = resp.status if resp else None
            final_url = page.url
            await context.close()
            await browser.close()
            return status, final_url, html, None
        except Exception as e:
            await context.close()
            await browser.close()
            return None, None, None, str(e)


async def crawl_public(root_url: str, max_pages: int = 10, js_render: bool = False, timeout_s: float = 10.0, respect_robots: bool = True) -> dict:
    headers = {"User-Agent": settings.user_agent}
    visited: set[str] = set()
    queue: list[str] = [root_url]
    pages: list[CrawledPage] = []

    blocked_pages_count = 0
    timeout_count = 0
    redirect_loops_count = 0
    server_errors_count = 0
    ssl_errors_count = 0
    anti_bot_detected = False
    spa_detected = False

    while queue and len(pages) < max_pages:
        url = queue.pop(0)
        if url in visited:
            continue
        visited.add(url)

        try:
            if js_render:
                status, final_url, html, err = await fetch_html_playwright(url, timeout_ms=int(timeout_s * 1000), user_agent=settings.user_agent)
            else:
                status, final_url, html, err = await fetch_html_httpx(url, timeout_s=timeout_s, headers=headers)
        except httpx.TimeoutException:
            timeout_count += 1
            pages.append(CrawledPage(url=url, status_code=None, final_url=None, html=None, title=None, description=None, h1=None, links=[], error="timeout"))
            continue
        except httpx.HTTPError as e:
            s = str(e).lower()
            if "ssl" in s or "certificate" in s:
                ssl_errors_count += 1
            pages.append(CrawledPage(url=url, status_code=None, final_url=None, html=None, title=None, description=None, h1=None, links=[], error=str(e)))
            continue

        if err:
            pages.append(CrawledPage(url=url, status_code=status, final_url=final_url, html=html, title=None, description=None, h1=None, links=[], error=err))
            continue

        if status in (403, 429):
            anti_bot_detected = True

        if status and 500 <= status <= 599:
            server_errors_count += 1

        title = desc = h1 = None
        raw_links: list[str] = []
        links: list[str] = []
        if html:
            title, desc, h1, raw_links = _extract_basic(html)
            if title is None and desc is None and h1 is None:
                soup = BeautifulSoup(html, "lxml")
                body_text = soup.get_text(" ", strip=True)
                if len(body_text) < 30:
                    spa_detected = True

            for href in raw_links:
                nu = _normalize_url(final_url or url, href)
                if nu and _same_site(root_url, nu) and nu not in visited:
                    links.append(nu)
        pages.append(CrawledPage(url=url, status_code=status, final_url=final_url, html=html, title=title, description=desc, h1=h1, links=links, error=None))

        for l in links:
            if len(queue) + len(pages) >= max_pages:
                break
            if l not in visited:
                queue.append(l)

        await asyncio.sleep(0)

    coverage = {
        "attempted": len(visited),
        "processed": len(pages),
        "max_pages": max_pages,
    }

    summary = {
        "coverage": coverage,
        "timeout_count": timeout_count,
        "ssl_errors_count": ssl_errors_count,
        "redirect_loops_count": redirect_loops_count,
        "server_errors_count": server_errors_count,
        "blocked_pages_count": blocked_pages_count,
        "anti_bot_detected": anti_bot_detected,
        "spa_detected": spa_detected,
        "js_render_used": js_render,
    }

    return {
        "root_url": root_url,
        "pages": [p.__dict__ for p in pages],
        "summary": summary,
    }