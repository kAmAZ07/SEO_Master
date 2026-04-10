import json
from datetime import date, timedelta
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from services.reporting_service.config import settings


def _load_credentials() -> Credentials:
    if not settings.gsc_credentials_json or not settings.gsc_token_json:
        raise ValueError("gsc_credentials_missing")
    info = json.loads(settings.gsc_credentials_json)
    token = json.loads(settings.gsc_token_json)
    return Credentials(
        token=token.get("token"),
        refresh_token=token.get("refresh_token"),
        token_uri=token.get("token_uri"),
        client_id=info.get("installed", {}).get("client_id") or info.get("web", {}).get("client_id"),
        client_secret=info.get("installed", {}).get("client_secret") or info.get("web", {}).get("client_secret"),
        scopes=["https://www.googleapis.com/auth/webmasters.readonly"],
    )


def fetch_gsc_summary(property_url: str, days: int = 28, offset_days: int = 0) -> dict:
    creds = _load_credentials()
    service = build("searchconsole", "v1", credentials=creds, cache_discovery=False)
    end = date.today() - timedelta(days=max(0, offset_days))
    start = end - timedelta(days=max(0, days - 1))
    body = {"startDate": start.isoformat(), "endDate": end.isoformat(), "dimensions": ["query"], "rowLimit": 50}
    resp = service.searchanalytics().query(siteUrl=property_url, body=body).execute()
    rows = resp.get("rows", []) or []
    clicks = sum(float(r.get("clicks", 0.0) or 0.0) for r in rows)
    impressions = sum(float(r.get("impressions", 0.0) or 0.0) for r in rows)
    ctr = 0.0 if impressions <= 0 else clicks / impressions
    positions = [float(r.get("position", 0.0) or 0.0) for r in rows if r.get("position") is not None]
    avg_position = None if not positions else round(sum(positions) / len(positions), 4)
    return {
        "property_url": property_url,
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
