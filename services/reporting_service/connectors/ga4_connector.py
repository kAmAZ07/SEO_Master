import json
from services.reporting_service.config import settings


def fetch_ga4_summary() -> dict:
    if not settings.ga4_property_id or not settings.ga4_credentials_json:
        return {"available": False, "reason": "ga4_credentials_missing"}
    try:
        json.loads(settings.ga4_credentials_json)
    except Exception:
        return {"available": False, "reason": "ga4_credentials_invalid"}
    return {"available": True, "property_id": settings.ga4_property_id, "note": "Stub implementation for учебный проект"}