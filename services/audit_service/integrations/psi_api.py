import httpx
from services.audit_service.config import settings


async def fetch_pagespeed_insights(url: str, strategy: str = "mobile") -> dict | None:
    if not settings.psi_api_key:
        return None

    api_url = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
    params = {"url": url, "strategy": strategy, "key": settings.psi_api_key}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(api_url, params=params)
            if r.status_code >= 400:
                return None
            j = r.json()
    except Exception:
        return None

    audits = (((j.get("lighthouseResult") or {}).get("audits")) or {})
    lcp = (audits.get("largest-contentful-paint") or {}).get("numericValue")
    fid = (audits.get("max-potential-fid") or {}).get("numericValue")
    cls = (audits.get("cumulative-layout-shift") or {}).get("numericValue")

    return {
        "metrics": {"LCP": int(lcp) if isinstance(lcp, (int, float)) else None, "FID": int(fid) if isinstance(fid, (int, float)) else None, "CLS": float(cls) if isinstance(cls, (int, float)) else None},
        "raw": j,
    }