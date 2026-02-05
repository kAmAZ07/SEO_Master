from urllib.parse import urljoin, urlparse

import httpx
from services.audit_service.config import settings


def _robots_url(root_url: str) -> str:
    p = urlparse(root_url)
    base = f"{p.scheme}://{p.netloc}"
    return urljoin(base, "/robots.txt")


def _blocked_root(robots_text: str) -> bool:
    lines = [l.strip() for l in robots_text.splitlines() if l.strip() and not l.strip().startswith("#")]
    ua_any = False
    disallows: list[str] = []
    for l in lines:
        if l.lower().startswith("user-agent:"):
            ua = l.split(":", 1)[1].strip()
            ua_any = (ua == "*" or ua == "")
        elif ua_any and l.lower().startswith("disallow:"):
            path = l.split(":", 1)[1].strip()
            disallows.append(path)
    return "/" in disallows


async def check_robots(root_url: str) -> dict:
    url = _robots_url(root_url)
    try:
        async with httpx.AsyncClient(headers={"User-Agent": settings.user_agent}, timeout=settings.default_timeout_s) as client:
            r = await client.get(url, follow_redirects=True)
            if r.status_code >= 400:
                return {"robots_url": url, "available": False, "blocked_root": False}
            txt = r.text or ""
            return {"robots_url": url, "available": True, "blocked_root": _blocked_root(txt)}
    except Exception:
        return {"robots_url": url, "available": False, "blocked_root": False}