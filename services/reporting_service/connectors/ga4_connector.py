from datetime import date
from typing import Any

from googleapiclient.discovery import build

from services.project_integrations.google_auth import build_google_credentials
from services.project_integrations.runtime import load_project_integration


def _load_integration(project_id: str) -> dict[str, Any]:
    return load_project_integration(project_id, "ga4")


def fetch_ga4_summary(project_id: str) -> dict:
    try:
        integration = _load_integration(project_id)
    except Exception as exc:
        return {"available": False, "reason": str(exc)}
    property_id = str(integration.get("property_id") or "").strip()
    if not property_id:
        return {"available": False, "reason": "ga4_property_id_missing"}
    return {
        "available": True,
        "property_id": property_id,
        "auth_mode": integration.get("auth_mode"),
        "account_identifier": integration.get("account_identifier"),
    }


def _load_credentials(project_id: str):
    integration = _load_integration(project_id)
    credentials_payload = dict(integration.get("credentials") or {})
    token_payload = integration.get("token") if isinstance(integration.get("token"), dict) else None
    if not credentials_payload:
        raise ValueError("ga4_credentials_missing")
    return build_google_credentials(
        credentials_payload,
        token_payload,
        scopes=["https://www.googleapis.com/auth/analytics.readonly"],
    )


def _degraded_result(reason: str, start_date: date, end_date: date) -> dict[str, Any]:
    return {
        "source": "ga4",
        "available": False,
        "status": "degraded",
        "reason": reason,
        "records": [],
        "aggregate": {
            "sessions": 0,
            "users": 0,
            "pageviews": 0,
            "conversions": 0,
            "revenue": 0.0,
            "avg_session_duration": 0.0,
            "bounce_rate": 0.0,
            "rows_count": 0,
            "period_start": start_date.isoformat(),
            "period_end": end_date.isoformat(),
        },
    }


def _ok_result(records: list[dict[str, Any]], start_date: date, end_date: date) -> dict[str, Any]:
    sessions = sum(int(row.get("sessions", 0) or 0) for row in records)
    users = sum(int(row.get("users", 0) or 0) for row in records)
    pageviews = sum(int(row.get("pageviews", 0) or 0) for row in records)
    conversions = sum(int(row.get("conversions", 0) or 0) for row in records)
    revenue = sum(float(row.get("revenue", 0.0) or 0.0) for row in records)
    durations = [float(row["avg_session_duration"]) for row in records if row.get("avg_session_duration") is not None]
    bounce_rates = [float(row["bounce_rate"]) for row in records if row.get("bounce_rate") is not None]
    return {
        "source": "ga4",
        "available": True,
        "status": "ok",
        "reason": None,
        "records": records,
        "aggregate": {
            "sessions": sessions,
            "users": users,
            "pageviews": pageviews,
            "conversions": conversions,
            "revenue": round(revenue, 2),
            "avg_session_duration": 0.0 if not durations else round(sum(durations) / len(durations), 4),
            "bounce_rate": 0.0 if not bounce_rates else round(sum(bounce_rates) / len(bounce_rates), 4),
            "rows_count": len(records),
            "period_start": start_date.isoformat(),
            "period_end": end_date.isoformat(),
        },
    }


def _metric_value(row: dict[str, Any], index: int, default: float = 0.0) -> float:
    metrics = row.get("metricValues", []) or []
    if len(metrics) <= index:
        return default
    try:
        return float(metrics[index].get("value", default) or default)
    except (TypeError, ValueError):
        return default


def fetch_ga4_rows(project_id: str, start_date: date, end_date: date, row_limit: int = 500) -> dict[str, Any]:
    try:
        integration = _load_integration(project_id)
        property_name = str(integration.get("property_id") or "").strip()
        if not property_name:
            raise ValueError("ga4_property_id_missing")
        creds = _load_credentials(project_id)
        if not property_name.startswith("properties/"):
            property_name = f"properties/{property_name}"
        service = build("analyticsdata", "v1beta", credentials=creds, cache_discovery=False)
        body = {
            "dateRanges": [{"startDate": start_date.isoformat(), "endDate": end_date.isoformat()}],
            "dimensions": [{"name": "date"}, {"name": "pagePath"}],
            "metrics": [
                {"name": "sessions"},
                {"name": "totalUsers"},
                {"name": "screenPageViews"},
                {"name": "averageSessionDuration"},
                {"name": "bounceRate"},
                {"name": "conversions"},
                {"name": "totalRevenue"},
            ],
            "limit": row_limit,
        }
        response = service.properties().runReport(property=property_name, body=body).execute()
    except Exception as exc:
        return _degraded_result(str(exc), start_date, end_date)

    records: list[dict[str, Any]] = []
    for row in response.get("rows", []) or []:
        dimensions = row.get("dimensionValues", []) or []
        raw_date = dimensions[0].get("value") if len(dimensions) > 0 else end_date.strftime("%Y%m%d")
        page_path = dimensions[1].get("value") if len(dimensions) > 1 else None
        row_date = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}" if len(raw_date) == 8 else end_date.isoformat()
        records.append(
            {
                "project_id": project_id,
                "date": row_date,
                "page_path": page_path,
                "sessions": int(_metric_value(row, 0)),
                "users": int(_metric_value(row, 1)),
                "pageviews": int(_metric_value(row, 2)),
                "avg_session_duration": _metric_value(row, 3),
                "bounce_rate": _metric_value(row, 4),
                "conversions": int(_metric_value(row, 5)),
                "revenue": _metric_value(row, 6),
                "raw_data": row,
            }
        )
    return _ok_result(records, start_date, end_date)
