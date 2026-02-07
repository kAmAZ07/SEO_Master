import asyncio
from urllib.parse import urlparse

import httpx
from services.audit_service.config import settings


def _is_internal(root_url: str, candidate: str) -> bool:
    r = urlparse(root_url)
    c = urlparse(candidate)
    return (r.scheme, r.netloc) == (c.scheme, c.netloc)


async def check_links_404(root_url: str, pages: list[dict]) -> tuple[list[dict], int]:
    findings = []
    internal_links: set[str] = set()

    for p in pages:
        for l in p.get("links", []) or []:
            if _is_internal(root_url, l):
                internal_links.add(l)
            if len(internal_links) >= settings.max_internal_link_checks:
                break
        if len(internal_links) >= settings.max_internal_link_checks:
            break

    links = list(internal_links)
    checked = 0

    async with httpx.AsyncClient(follow_redirects=True, headers={"User-Agent": settings.user_agent}, timeout=settings.default_timeout_s) as client:
        sem = asyncio.Semaphore(10)

        async def _probe(u: str):
            nonlocal checked
            async with sem:
                try:
                    r = await client.head(u)
                    checked += 1
                    if r.status_code == 404:
                        findings.append({"code": "broken_link_404", "severity": "medium", "confidence": "high", "details": {"url": u}})
                    if r.status_code in (403, 429):
                        findings.append({"code": "link_check_blocked", "severity": "info", "confidence": "high", "details": {"url": u, "status": r.status_code}})
                except httpx.HTTPError as e:
                    checked += 1
                    findings.append({"code": "link_check_error", "severity": "info", "confidence": "medium", "details": {"url": u, "error": str(e)}})

        await asyncio.gather(*[_probe(u) for u in links])

    return findings, checked