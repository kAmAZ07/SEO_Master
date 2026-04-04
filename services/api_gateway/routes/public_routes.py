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
        logger.warning("Redis client init failed; rate-limit disabled", extra={"error": str(exc)})
        return None


redis_client = _build_redis_client()


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


async def check_rate_limit(request: Request) -> bool:
    if redis_client is None:
        return True

    client_ip = request.client.host if request.client else "unknown"
    key = f"rate_limit:public_audit:{client_ip}"

    try:
        current_count = redis_client.get(key)
    except Exception as exc:
        logger.warning("Rate-limit check failed; allowing request", extra={"error": str(exc)})
        return True

    if current_count and int(current_count) >= settings.PUBLIC_RATE_LIMIT:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "Rate limit exceeded",
                "limit": settings.PUBLIC_RATE_LIMIT,
                "window_seconds": settings.PUBLIC_RATE_LIMIT_WINDOW_SECONDS,
                "message": f"Maximum {settings.PUBLIC_RATE_LIMIT} audits per hour.",
            },
        )

    return True


@router.post("/quick-audit", response_model=Dict[str, Any])
async def create_quick_audit(
    audit_request: QuickAuditRequest,
    request: Request,
    _: bool = Depends(check_rate_limit),
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

        if redis_client is not None:
            client_ip = request.client.host if request.client else "unknown"
            key = f"rate_limit:public_audit:{client_ip}"
            try:
                pipe = redis_client.pipeline()
                pipe.incr(key)
                pipe.expire(key, settings.PUBLIC_RATE_LIMIT_WINDOW_SECONDS)
                pipe.execute()
            except Exception as exc:
                logger.warning("Rate-limit increment failed", extra={"error": str(exc)})

        return {
            "success": True,
            "uid": audit_uid,
            "audit_id": audit_uid,
            "status": "processing",
            "message": "Audit started",
            "estimated_time_seconds": settings.PUBLIC_AUDIT_TIMEOUT_SECONDS,
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

        return AuditStatusResponse(
            uid=uid,
            status=status_value,
            progress=_status_to_progress(status_value),
            message=_status_to_message(status_value),
            results={
                "summary": result.get("summary", {}),
                "findings": result.get("findings", []),
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
    if redis_client is None:
        return {
            "limit": settings.PUBLIC_RATE_LIMIT,
            "remaining": settings.PUBLIC_RATE_LIMIT,
            "reset_in_seconds": settings.PUBLIC_RATE_LIMIT_WINDOW_SECONDS,
            "window_seconds": settings.PUBLIC_RATE_LIMIT_WINDOW_SECONDS,
            "mode": "disabled",
        }

    client_ip = request.client.host if request.client else "unknown"
    key = f"rate_limit:public_audit:{client_ip}"

    try:
        current_count = redis_client.get(key)
        remaining = settings.PUBLIC_RATE_LIMIT - (int(current_count) if current_count else 0)
        ttl = redis_client.ttl(key)
    except Exception as exc:
        logger.warning("Rate-limit info read failed", extra={"error": str(exc)})
        remaining = settings.PUBLIC_RATE_LIMIT
        ttl = settings.PUBLIC_RATE_LIMIT_WINDOW_SECONDS

    return {
        "limit": settings.PUBLIC_RATE_LIMIT,
        "remaining": max(0, remaining),
        "reset_in_seconds": ttl if ttl and ttl > 0 else settings.PUBLIC_RATE_LIMIT_WINDOW_SECONDS,
        "window_seconds": settings.PUBLIC_RATE_LIMIT_WINDOW_SECONDS,
    }
