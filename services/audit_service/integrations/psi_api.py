import httpx
from services.audit_service.config import settings


async def fetch_pagespeed_insights(url: str, strategy: str = "mobile") -> dict | None:
    api_url = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
    params = {"url": url, "strategy": strategy}
    if settings.psi_api_key:
        params["key"] = settings.psi_api_key
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
    # INP replaced FID as a Core Web Vital in March 2024. Keep the old FID
    # audit as a fallback for cached/test PageSpeed payloads and older reports.
    inp = (audits.get("interaction-to-next-paint") or {}).get("numericValue")
    fid = (audits.get("max-potential-fid") or {}).get("numericValue")
    interaction = inp if isinstance(inp, (int, float)) else fid
    cls = (audits.get("cumulative-layout-shift") or {}).get("numericValue")

    return {
        "metrics": {
            "LCP": int(lcp) if isinstance(lcp, (int, float)) else None,
            "INP": int(interaction) if isinstance(interaction, (int, float)) else None,
            "FID": int(fid) if isinstance(fid, (int, float)) else None,
            "CLS": float(cls) if isinstance(cls, (int, float)) else None,
        },
        "raw": j,
        "used_api_key": bool(settings.psi_api_key),
    }
