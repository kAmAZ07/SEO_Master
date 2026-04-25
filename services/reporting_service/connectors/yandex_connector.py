from datetime import date
from typing import Any

import httpx

from services.project_integrations.runtime import load_project_integration


def _load_integration(project_id: str) -> dict[str, Any]:
    return load_project_integration(project_id, "yandex")


def fetch_yandex_summary(project_id: str) -> dict:
    try:
        integration = _load_integration(project_id)
    except Exception as exc:
        return {"available": False, "reason": str(exc)}
    host_id = str(integration.get("host_id") or "").strip()
    if not host_id:
        return {"available": False, "reason": "yandex_host_id_missing"}
    return {
        "available": True,
        "host_id": host_id,
        "user_id": integration.get("user_id"),
    }


def _degraded_result(reason: str, start_date: date, end_date: date) -> dict[str, Any]:
    return {
        "source": "yandex",
        "available": False,
        "status": "degraded",
        "reason": reason,
        "records": [],
        "aggregate": {
            "shows": 0,
            "clicks": 0,
            "ctr": 0.0,
            "avg_position": None,
            "rows_count": 0,
            "period_start": start_date.isoformat(),
            "period_end": end_date.isoformat(),
        },
    }


def _ok_result(records: list[dict[str, Any]], start_date: date, end_date: date) -> dict[str, Any]:
    shows = sum(int(row.get("shows", 0) or 0) for row in records)
    clicks = sum(int(row.get("clicks", 0) or 0) for row in records)
    positions = [float(row["position"]) for row in records if row.get("position") is not None]
    ctr = 0.0 if shows <= 0 else clicks / shows
    return {
        "source": "yandex",
        "available": True,
        "status": "ok",
        "reason": None,
        "records": records,
        "aggregate": {
            "shows": shows,
            "clicks": clicks,
            "ctr": round(ctr, 4),
            "avg_position": None if not positions else round(sum(positions) / len(positions), 4),
            "rows_count": len(records),
            "period_start": start_date.isoformat(),
            "period_end": end_date.isoformat(),
        },
    }


def _metric(payload: dict[str, Any], *keys: str) -> float:
    current: Any = payload
    for key in keys:
        if not isinstance(current, dict):
            return 0.0
        current = current.get(key)
    try:
        return float(current or 0.0)
    except (TypeError, ValueError):
        return 0.0


def fetch_yandex_rows(project_id: str, start_date: date, end_date: date, row_limit: int = 500) -> dict[str, Any]:
    try:
        integration = _load_integration(project_id)
        token = str(integration.get("token") or "").strip()
        user_id = str(integration.get("user_id") or "").strip()
        host_id = str(integration.get("host_id") or "").strip()
        if not token or not user_id or not host_id:
            raise ValueError("yandex_credentials_missing")
        url = (
            f"https://api.webmaster.yandex.net/v4/user/{user_id}"
            f"/hosts/{host_id}/search-queries/all/history"
        )
        response = httpx.get(
            url,
            params={"date_from": start_date.isoformat(), "date_to": end_date.isoformat()},
            headers={"Authorization": f"OAuth {token}"},
            timeout=20.0,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        return _degraded_result(str(exc), start_date, end_date)

    source_rows = payload.get("queries") or payload.get("indicators") or []
    if not isinstance(source_rows, list):
        return _degraded_result("yandex_response_shape_unexpected", start_date, end_date)

    records: list[dict[str, Any]] = []
    for item in source_rows[:row_limit]:
        if not isinstance(item, dict):
            continue
        query = item.get("query_text") or item.get("query") or item.get("text")
        row_date = item.get("date") or item.get("period") or end_date.isoformat()
        indicators = item.get("indicators") if isinstance(item.get("indicators"), dict) else item
        shows = int(_metric(indicators, "TOTAL_SHOWS") or _metric(indicators, "shows") or _metric(indicators, "show_count"))
        clicks = int(_metric(indicators, "TOTAL_CLICKS") or _metric(indicators, "clicks") or _metric(indicators, "click_count"))
        position = _metric(indicators, "AVG_SHOW_POSITION") or _metric(indicators, "position")
        records.append(
            {
                "project_id": project_id,
                "date": row_date[:10],
                "query": query,
                "url": item.get("url"),
                "shows": shows,
                "clicks": clicks,
                "ctr": 0.0 if shows <= 0 else clicks / shows,
                "position": None if position == 0.0 else position,
                "raw_data": item,
            }
        )
    return _ok_result(records, start_date, end_date)
