import json
from datetime import date, timedelta

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from services.audit_service.config import settings


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


def analyze_links(property_url: str, days: int = 28) -> dict:
    creds = _load_credentials()
    service = build("searchconsole", "v1", credentials=creds, cache_discovery=False)
    end = date.today()
    start = end - timedelta(days=days)
    body = {
        "startDate": start.isoformat(),
        "endDate": end.isoformat(),
        "dimensions": ["linkingSite"],
        "rowLimit": 50,
    }
    resp = service.searchanalytics().query(siteUrl=property_url, body=body).execute()
    rows = resp.get("rows", []) or []
    top = []
    for r in rows:
        keys = r.get("keys", []) or []
        top.append({"linking_site": keys[0] if keys else None, "clicks": r.get("clicks"), "impressions": r.get("impressions")})
    return {"property_url": property_url, "range_days": days, "top_linking_sites": top}