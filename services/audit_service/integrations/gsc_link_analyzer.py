from datetime import date, timedelta
from typing import Any

from googleapiclient.discovery import build

from services.project_integrations.google_auth import build_google_credentials
from services.project_integrations.runtime import load_project_integration


def _load_integration(project_id: str) -> dict[str, Any]:
    if not project_id:
        raise ValueError("gsc_project_id_missing")
    return load_project_integration(project_id, "gsc")


def analyze_links(project_id: str, property_url: str | None = None, days: int = 28) -> dict[str, Any]:
    integration = _load_integration(project_id)
    resolved_property_url = str(integration.get("property_url") or property_url or "").strip()
    if not resolved_property_url:
        raise ValueError("gsc_property_url_missing")

    credentials_payload = dict(integration.get("credentials") or {})
    token_payload = integration.get("token") if isinstance(integration.get("token"), dict) else None
    if not credentials_payload:
        raise ValueError("gsc_credentials_missing")

    creds = build_google_credentials(
        credentials_payload,
        token_payload,
        scopes=["https://www.googleapis.com/auth/webmasters.readonly"],
    )
    service = build("searchconsole", "v1", credentials=creds, cache_discovery=False)
    end = date.today()
    start = end - timedelta(days=days)
    body = {
        "startDate": start.isoformat(),
        "endDate": end.isoformat(),
        "dimensions": ["linkingSite"],
        "rowLimit": 50,
    }
    resp = service.searchanalytics().query(siteUrl=resolved_property_url, body=body).execute()
    rows = resp.get("rows", []) or []
    top: list[dict[str, Any]] = []
    for row in rows:
        keys = row.get("keys", []) or []
        top.append(
            {
                "linking_site": keys[0] if keys else None,
                "clicks": row.get("clicks"),
                "impressions": row.get("impressions"),
            }
        )
    return {"property_url": resolved_property_url, "range_days": days, "top_linking_sites": top}
