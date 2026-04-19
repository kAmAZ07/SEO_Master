from datetime import datetime
from typing import Any, Dict, Optional
import httpx
import redis
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, validator

from config.logging_config import get_logger
from services.api_gateway.config import settings, get_redis_config

logger = get_logger(__name__)
router = APIRouter(prefix="/api/public", tags=["Public"])

RATE_LIMIT_SCRIPT = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then
  redis.call('EXPIRE', KEYS[1], ARGV[1])
end
local ttl = redis.call('TTL', KEYS[1])
if ttl < 0 then
  redis.call('EXPIRE', KEYS[1], ARGV[1])
  ttl = tonumber(ARGV[1])
end
return {current, ttl}
"""


def _build_redis_client() -> Optional[redis.Redis]:
    try:
        cfg = get_redis_config()
        return redis.Redis(
            host=cfg["host"],
            port=cfg["port"],
            password=cfg["password"],
            db=cfg["db"],
            decode_responses=True,
            socket_connect_timeout=1,
            socket_timeout=1,
        )
    except Exception as exc:
        logger.error("Redis client init failed; public audit rate-limit unavailable", extra={"error": str(exc)})
        return None


redis_client = _build_redis_client()


def _rate_limit_unavailable(exc: Exception | None = None) -> HTTPException:
    if exc:
        logger.error("Public audit rate-limit unavailable", extra={"error": str(exc)})
    return HTTPException(
        status_code=503,
        detail={
            "error": "rate_limit_unavailable",
            "message": "Public audit rate limiting is temporarily unavailable. Please try again later.",
        },
    )


def _get_redis_client() -> redis.Redis:
    global redis_client
    if redis_client is None:
        redis_client = _build_redis_client()
    if redis_client is None:
        raise _rate_limit_unavailable()
    return redis_client


def _rate_limit_key(request: Request) -> str:
    client_ip = request.client.host if request.client else "unknown"
    return f"rate_limit:public_audit:{client_ip}"


def _rate_limit_headers(limit: int, remaining: int, reset_in_seconds: int) -> Dict[str, str]:
    return {
        "X-RateLimit-Limit": str(limit),
        "X-RateLimit-Remaining": str(max(0, remaining)),
        "X-RateLimit-Reset": str(reset_in_seconds),
        "Retry-After": str(reset_in_seconds),
    }


class QuickAuditRequest(BaseModel):
    url: str = Field(..., description="Website URL to audit")
    email: Optional[str] = Field(None, description="Optional email for notifications")

    @validator("url")
    def validate_url(cls, v: str) -> str:
        v = v.strip().lower()
        if not v.startswith(("http://", "https://")):
            raise ValueError("URL must start with http:// or https://")

        blocked = ("localhost", "127.0.0.1", "192.168.", "10.", "172.16.", "172.31.")
        if any(token in v for token in blocked):
            raise ValueError("Local and private network URLs are not allowed")

        return v


class AuditStatusResponse(BaseModel):
    uid: str
    status: str
    progress: int = 0
    message: str = ""
    results: Optional[Dict[str, Any]] = None
    created_at: str
    completed_at: Optional[str] = None


def _status_to_progress(status: str) -> int:
    if status == "completed":
        return 100
    if status == "running":
        return 60
    if status == "queued":
        return 10
    if status == "failed":
        return 100
    return 0


def _status_to_message(status: str) -> str:
    messages = {
        "queued": "Audit queued",
        "running": "Audit in progress",
        "completed": "Audit completed",
        "failed": "Audit failed",
    }
    return messages.get(status, "Audit status unknown")


async def check_rate_limit(request: Request) -> Dict[str, int]:
    client = _get_redis_client()
    key = _rate_limit_key(request)
    limit = settings.PUBLIC_RATE_LIMIT
    window = settings.PUBLIC_RATE_LIMIT_WINDOW_SECONDS

    try:
        current_count, ttl = client.eval(RATE_LIMIT_SCRIPT, 1, key, window)
    except Exception as exc:
        raise _rate_limit_unavailable(exc) from exc

    current = int(current_count or 0)
    reset_in_seconds = int(ttl or window)
    if reset_in_seconds <= 0:
        reset_in_seconds = window

    remaining = max(0, limit - current)
    if current > limit:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "Rate limit exceeded",
                "limit": limit,
                "remaining": 0,
                "reset_in_seconds": reset_in_seconds,
                "window_seconds": window,
                "message": f"Maximum {limit} audits per hour.",
            },
            headers=_rate_limit_headers(limit, 0, reset_in_seconds),
        )

    return {
        "limit": limit,
        "remaining": remaining,
        "reset_in_seconds": reset_in_seconds,
        "window_seconds": window,
    }


@router.post("/quick-audit", response_model=Dict[str, Any])
async def create_quick_audit(
    audit_request: QuickAuditRequest,
    rate_limit: Dict[str, int] = Depends(check_rate_limit),
):
    try:
        payload = {
            "root_url": audit_request.url,
            "site_type_hint": "unknown",
            "platform": "generic",
            "options": {
                "max_pages": settings.PUBLIC_AUDIT_MAX_PAGES,
                "max_depth": 2,
                "js_render": False,
                "timeout": float(settings.PUBLIC_AUDIT_TIMEOUT_SECONDS),
            },
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{settings.AUDIT_SERVICE_URL}/audit/public",
                json=payload,
            )
            response.raise_for_status()
            result = response.json()

        audit_uid = result.get("audit_id")
        if not audit_uid:
            raise HTTPException(status_code=502, detail="Audit service returned invalid response")

        return {
            "success": True,
            "uid": audit_uid,
            "audit_id": audit_uid,
            "status": "processing",
            "message": "Audit started",
            "estimated_time_seconds": settings.PUBLIC_AUDIT_TIMEOUT_SECONDS,
            "rate_limit": rate_limit,
        }

    except httpx.HTTPStatusError as exc:
        logger.error("Audit service error", extra={"status_code": exc.response.status_code, "body": exc.response.text[:500]})
        raise HTTPException(status_code=exc.response.status_code, detail="Failed to start audit") from exc
    except httpx.RequestError as exc:
        logger.error(f"Failed to connect to Audit Service: {exc}")
        raise HTTPException(status_code=503, detail="Audit service temporarily unavailable") from exc


@router.get("/audit-status/{uid}", response_model=AuditStatusResponse)
async def get_audit_status(uid: str):
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{settings.AUDIT_SERVICE_URL}/audit/{uid}")

        if response.status_code == 404:
            raise HTTPException(status_code=404, detail="Audit not found or expired")

        response.raise_for_status()
        result = response.json()

        status_value = result.get("status", "unknown")
        created_at = result.get("created_at") or datetime.utcnow().isoformat()
        completed_at = result.get("updated_at") if status_value in {"completed", "failed"} else None
        all_findings = result.get("findings", [])
        top_findings = result.get("top_findings")
        if not isinstance(top_findings, list):
            top_findings = all_findings[:5] if isinstance(all_findings, list) else []

        return AuditStatusResponse(
            uid=uid,
            status=status_value,
            progress=_status_to_progress(status_value),
            message=_status_to_message(status_value),
            results={
                "summary": result.get("summary", {}),
                "findings": top_findings,
                "top_findings": top_findings,
                "findings_count": len(all_findings) if isinstance(all_findings, list) else 0,
                "pages": result.get("pages", []),
            },
            created_at=created_at,
            completed_at=completed_at,
        )

    except httpx.RequestError as exc:
        logger.error(f"Failed to connect to Audit Service: {exc}")
        raise HTTPException(status_code=503, detail="Audit service temporarily unavailable") from exc


@router.get("/rate-limit-info")
async def get_rate_limit_info(request: Request):
    client = _get_redis_client()
    key = _rate_limit_key(request)

    try:
        current_count = client.get(key)
        remaining = settings.PUBLIC_RATE_LIMIT - (int(current_count) if current_count else 0)
        ttl = client.ttl(key)
    except Exception as exc:
        raise _rate_limit_unavailable(exc) from exc

    return {
        "limit": settings.PUBLIC_RATE_LIMIT,
        "remaining": max(0, remaining),
        "reset_in_seconds": ttl if ttl and ttl > 0 else settings.PUBLIC_RATE_LIMIT_WINDOW_SECONDS,
        "window_seconds": settings.PUBLIC_RATE_LIMIT_WINDOW_SECONDS,
        "mode": "redis",
    }
