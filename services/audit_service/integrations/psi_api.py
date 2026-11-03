import httpx
from services.audit_service.config import settings

PSI_URL = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"


async def fetch_pagespeed_result(url: str, strategy: str = "mobile", timeout: float = 15.0) -> tuple[dict | None, str | None]:
    """Возвращает (данные, причина недоступности). Причины: quota_exceeded, unavailable, invalid_response."""
    params = {"url": url, "strategy": strategy}
    if settings.psi_api_key:
        params["key"] = settings.psi_api_key
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.get(PSI_URL, params=params)
        if r.status_code == 429:
            return None, "quota_exceeded"
        if r.status_code >= 400:
            return None, "unavailable"
        j = r.json()
    except httpx.HTTPError:
        return None, "unavailable"
    except ValueError:
        return None, "invalid_response"
    if not isinstance(j, dict):
        return None, "invalid_response"

    audits = (((j.get("lighthouseResult") or {}).get("audits")) or {})
    lcp = (audits.get("largest-contentful-paint") or {}).get("numericValue")
    # INP заменил FID в Core Web Vitals в марте 2024. Старый аудит FID остаётся отдельным полем
    # и не подменяет INP; значение INP берётся только из interaction-to-next-paint.
    inp = (audits.get("interaction-to-next-paint") or {}).get("numericValue")
    fid = (audits.get("max-potential-fid") or {}).get("numericValue")
    cls = (audits.get("cumulative-layout-shift") or {}).get("numericValue")
    field_data = bool((j.get("loadingExperience") or {}).get("metrics"))

    return {
        "metrics": {
            "LCP": int(lcp) if isinstance(lcp, (int, float)) else None,
            "INP": int(inp) if isinstance(inp, (int, float)) else None,
            "FID": int(fid) if isinstance(fid, (int, float)) else None,
            "CLS": float(cls) if isinstance(cls, (int, float)) else None,
        },
        "source": "pagespeed_insights_lighthouse_lab",
        "field_data_available": field_data,
        "raw": j,
        "used_api_key": bool(settings.psi_api_key),
    }, None


async def fetch_pagespeed_insights(url: str, strategy: str = "mobile") -> dict | None:
    data, _ = await fetch_pagespeed_result(url, strategy)
    return data
