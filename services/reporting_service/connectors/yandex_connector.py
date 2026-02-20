from services.reporting_service.config import settings


def fetch_yandex_summary() -> dict:
    if not settings.yandex_token:
        return {"available": False, "reason": "yandex_token_missing"}
    return {"available": True, "note": "Stub implementation for учебный проект"}