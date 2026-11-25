from datetime import date, timedelta
from typing import Any

from googleapiclient.discovery import Resource, build

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


def _property_aggregate(service: Resource, property_url: str, start: date, end: date) -> dict[str, Any]:
    body = {"startDate": start.isoformat(), "endDate": end.isoformat(), "aggregationType": "byProperty",
            "dataState": "final", "rowLimit": 1}
    resp = service.searchanalytics().query(siteUrl=property_url, body=body).execute()
    rows = resp.get("rows", []) or []
    row = rows[0] if rows else {}
    return {
        "status": "ok" if rows else "no_data",
        "aggregation": "by_property",
        "data_state": "final",
        "clicks": row.get("clicks"),
        "impressions": row.get("impressions"),
        "ctr": row.get("ctr"),
        "avg_position": row.get("position"),
        "rows_count": len(rows),
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
    }


def fetch_gsc_summary(project_id: str, property_url: str | None = None, days: int = 28, offset_days: int = 0) -> dict:
    _, resolved_property_url = _resolve_property_url(project_id, property_url)
    creds = _load_credentials(project_id)
    service = build("searchconsole", "v1", credentials=creds, cache_discovery=False)
    end = date.today() - timedelta(days=max(0, offset_days))
    start = end - timedelta(days=max(0, days - 1))
    aggregate = _property_aggregate(service, resolved_property_url, start, end)
    return {
        **aggregate,
        "property_url": resolved_property_url,
        "range_days": (end - start).days + 1,
        "offset_days": offset_days,
        "start_date": aggregate["period_start"],
        "end_date": aggregate["period_end"],
    }


def _degraded_result(reason: str, start_date: date, end_date: date) -> dict[str, Any]:
    return {
        "source": "gsc",
        "available": False,
        "status": "degraded",
        "reason": reason,
        "records": [],
        "aggregate": {
            "clicks": None,
            "impressions": None,
            "ctr": None,
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
        aggregate = _property_aggregate(service, resolved_property_url, start_date, end_date)
    except Exception:
        return _degraded_result("gsc_property_aggregate_unavailable", start_date, end_date)

    try:
        body = {
            "startDate": start_date.isoformat(),
            "endDate": end_date.isoformat(),
            "dimensions": ["date", "query", "page"],
            "dataState": "final",
            "rowLimit": row_limit,
        }
        resp = service.searchanalytics().query(siteUrl=resolved_property_url, body=body).execute()
    except Exception:
        return {
            "source": "gsc",
            "available": aggregate["status"] == "ok",
            "status": "partial",
            "reason": "gsc_detail_unavailable",
            "records": [],
            "aggregate": aggregate,
            "detail_coverage": "unavailable",
        }

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
    return {
        "source": "gsc",
        "available": aggregate["status"] == "ok",
        "status": aggregate["status"],
        "reason": None if aggregate["status"] == "ok" else "gsc_no_data",
        "records": records,
        "aggregate": aggregate,
        "detail_coverage": "limited",
    }
