from datetime import datetime, timedelta, timezone
from ipaddress import ip_address, ip_network
from typing import Any, Dict

from fastapi import HTTPException, Request, status
from sqlalchemy.orm import Session

from services.client_api_gateway.config import is_production, settings
from services.client_api_gateway.db.models import DeploymentLog


def get_client_ip(request: Request) -> str:
    if settings.CLIENT_API_TRUST_PROXY_HEADERS:
        forwarded_for = request.headers.get("x-forwarded-for")
        if forwarded_for:
            return forwarded_for.split(",", 1)[0].strip()

        real_ip = request.headers.get("x-real-ip")
        if real_ip:
            return real_ip.strip()

    return request.client.host if request.client else "unknown"


def _whitelist_entries() -> list[str]:
    return [item.strip() for item in settings.CLIENT_API_IP_WHITELIST.split(",") if item.strip()]


def require_patch_ip_whitelisted(request: Request) -> None:
    allowed_entries = _whitelist_entries()
    if not allowed_entries:
        if is_production():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="CLIENT_API_IP_WHITELIST is required for production PATCH endpoints",
            )
        return

    if "*" in allowed_entries:
        return

    client_ip = get_client_ip(request)
    try:
        parsed_ip = ip_address(client_ip)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid client IP") from exc

    for entry in allowed_entries:
        try:
            if "/" in entry:
                if parsed_ip in ip_network(entry, strict=False):
                    return
            elif parsed_ip == ip_address(entry):
                return
        except ValueError:
            continue

    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Client IP is not allowed")


def enforce_project_rate_limit(db: Session, project_id: str, now: datetime | None = None) -> Dict[str, Any]:
    limit = settings.CLIENT_API_RATE_LIMIT_PER_PROJECT
    window_seconds = settings.CLIENT_API_RATE_LIMIT_WINDOW_SECONDS
    if limit <= 0 or window_seconds <= 0:
        return {"enabled": False}

    now = now or datetime.now(timezone.utc)
    window_start = now - timedelta(seconds=window_seconds)
    request_count = (
        db.query(DeploymentLog)
        .filter(
            DeploymentLog.project_id == project_id,
            DeploymentLog.created_at >= window_start,
        )
        .count()
    )

    if request_count >= limit:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "error": "rate_limit_exceeded",
                "project_id": project_id,
                "limit": limit,
                "window_seconds": window_seconds,
                "message": f"Maximum {limit} PATCH requests per project per hour.",
            },
        )

    return {
        "enabled": True,
        "limit": limit,
        "remaining": max(limit - request_count - 1, 0),
        "window_seconds": window_seconds,
        "window_start": window_start.isoformat(),
    }
