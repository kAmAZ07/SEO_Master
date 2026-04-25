from datetime import date, timedelta
from typing import Any

from googleapiclient.discovery import build

from services.project_integrations.google_auth import build_google_credentials
from services.project_integrations.runtime import load_project_integration


def _load_integration(project_id: str) -> dict[str, Any]:
    return load_project_integration(project_id, "gsc")


def _resolve_property_url(project_id: str, fallback_property_url: str | None = None) -> tuple[dict[str, Any], str]:
    integration = _load_integration(project_id)
    property_url = str(integration.get("property_url") or fallback_property_url or "").strip()
    if not property_url:
        raise ValueError("gsc_property_url_missing")
    return integration, property_url


def _load_credentials(project_id: str):
    integration = _load_integration(project_id)
    credentials_payload = dict(integration.get("credentials") or {})
    token_payload = integration.get("token") if isinstance(integration.get("token"), dict) else None
    if not credentials_payload:
        raise ValueError("gsc_credentials_missing")
    return build_google_credentials(
        credentials_payload,
        token_payload,
        scopes=["https://www.googleapis.com/auth/webmasters.readonly"],
    )


def fetch_gsc_summary(project_id: str, property_url: str | None = None, days: int = 28, offset_days: int = 0) -> dict:
    _, resolved_property_url = _resolve_property_url(project_id, property_url)
    creds = _load_credentials(project_id)
    service = build("searchconsole", "v1", credentials=creds, cache_discovery=False)
    end = date.today() - timedelta(days=max(0, offset_days))
    start = end - timedelta(days=max(0, days - 1))
    body = {"startDate": start.isoformat(), "endDate": end.isoformat(), "dimensions": ["query"], "rowLimit": 50}
    resp = service.searchanalytics().query(siteUrl=resolved_property_url, body=body).execute()
    rows = resp.get("rows", []) or []
    clicks = sum(float(r.get("clicks", 0.0) or 0.0) for r in rows)
    impressions = sum(float(r.get("impressions", 0.0) or 0.0) for r in rows)
    ctr = 0.0 if impressions <= 0 else clicks / impressions
    positions = [float(r.get("position", 0.0) or 0.0) for r in rows if r.get("position") is not None]
    avg_position = None if not positions else round(sum(positions) / len(positions), 4)
    return {
        "property_url": resolved_property_url,
        "range_days": days,
        "offset_days": offset_days,
        "clicks": round(clicks, 2),
        "impressions": round(impressions, 2),
        "ctr": round(ctr, 4),
        "avg_position": avg_position,
        "rows_count": len(rows),
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
    }


def _ok_result(records: list[dict[str, Any]], start_date: date, end_date: date) -> dict[str, Any]:
    clicks = sum(int(row.get("clicks", 0) or 0) for row in records)
    impressions = sum(int(row.get("impressions", 0) or 0) for row in records)
    positions = [float(row["position"]) for row in records if row.get("position") is not None]
    ctr = 0.0 if impressions <= 0 else clicks / impressions
    return {
        "source": "gsc",
        "available": True,
        "status": "ok",
        "reason": None,
        "records": records,
        "aggregate": {
            "clicks": clicks,
            "impressions": impressions,
            "ctr": round(ctr, 4),
            "avg_position": None if not positions else round(sum(positions) / len(positions), 4),
            "rows_count": len(records),
            "period_start": start_date.isoformat(),
            "period_end": end_date.isoformat(),
        },
    }


def _degraded_result(reason: str, start_date: date, end_date: date) -> dict[str, Any]:
    return {
        "source": "gsc",
        "available": False,
        "status": "degraded",
        "reason": reason,
        "records": [],
        "aggregate": {
            "clicks": 0,
            "impressions": 0,
            "ctr": 0.0,
            "avg_position": None,
            "rows_count": 0,
            "period_start": start_date.isoformat(),
            "period_end": end_date.isoformat(),
        },
    }


def fetch_gsc_rows(
    project_id: str,
    property_url: str,
    start_date: date,
    end_date: date,
    row_limit: int = 500,
) -> dict[str, Any]:
    try:
        _, resolved_property_url = _resolve_property_url(project_id, property_url)
        creds = _load_credentials(project_id)
        service = build("searchconsole", "v1", credentials=creds, cache_discovery=False)
        body = {
            "startDate": start_date.isoformat(),
            "endDate": end_date.isoformat(),
            "dimensions": ["date", "query", "page"],
            "rowLimit": row_limit,
        }
        resp = service.searchanalytics().query(siteUrl=resolved_property_url, body=body).execute()
    except Exception as exc:
        return _degraded_result(str(exc), start_date, end_date)

    records: list[dict[str, Any]] = []
    for row in resp.get("rows", []) or []:
        keys = list(row.get("keys", []) or [])
        row_date = keys[0] if len(keys) > 0 else end_date.isoformat()
        query = keys[1] if len(keys) > 1 else None
        page = keys[2] if len(keys) > 2 else None
        records.append(
            {
                "project_id": project_id,
                "date": row_date,
                "query": query,
                "page": page,
                "clicks": int(row.get("clicks", 0) or 0),
                "impressions": int(row.get("impressions", 0) or 0),
                "ctr": float(row.get("ctr", 0.0) or 0.0),
                "position": None if row.get("position") is None else float(row.get("position")),
                "raw_data": {"keys": keys, **{key: row.get(key) for key in ("clicks", "impressions", "ctr", "position")}},
            }
        )
    return _ok_result(records, start_date, end_date)
