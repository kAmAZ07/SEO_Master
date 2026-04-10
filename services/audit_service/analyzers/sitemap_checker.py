from urllib.parse import urljoin, urlparse

import httpx

from services.audit_service.config import settings


def _sitemap_url(root_url: str) -> str:
    parsed = urlparse(root_url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    return urljoin(base, "/sitemap.xml")


async def check_sitemap(root_url: str) -> dict:
    url = _sitemap_url(root_url)
    try:
        async with httpx.AsyncClient(headers={"User-Agent": settings.user_agent}, timeout=settings.default_timeout_s) as client:
            response = await client.get(url, follow_redirects=True)
            body = (response.text or "").strip()
            content_type = response.headers.get("content-type", "")
            looks_like_xml = body.startswith("<?xml") or "<urlset" in body or "<sitemapindex" in body
            available = response.status_code < 400 and looks_like_xml
            return {
                "sitemap_url": url,
                "available": available,
                "status_code": response.status_code,
                "content_type": content_type,
            }
    except Exception:
        return {"sitemap_url": url, "available": False, "status_code": None, "content_type": None}
