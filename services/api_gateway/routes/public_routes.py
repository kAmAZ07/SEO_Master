from fastapi import APIRouter, HTTPException, Request, Depends
from pydantic import BaseModel, Field, validator
from sqlalchemy.orm import Session
from typing import Optional, Dict, Any
from datetime import datetime
import httpx
import redis
import uuid
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from config.database_config import get_db
from config.logging_config import get_logger
from services.api_gateway.config import settings, get_redis_config

logger = get_logger(__name__)
router = APIRouter(prefix="/api/public", tags=["Public"])


redis_client = redis.Redis(
    host=get_redis_config()["host"],
    port=get_redis_config()["port"],
    password=get_redis_config()["password"],
    db=get_redis_config()["db"],
    decode_responses=True
)


class QuickAuditRequest(BaseModel):
    url: str = Field(..., description="Website URL to audit")
    email: Optional[str] = Field(None, description="Email for results notification")
    
    @validator("url")
    def validate_url(cls, v):
        if not v.startswith(("http://", "https://")):
            raise ValueError("URL must start with http:// or https://")
        
        if "localhost" in v.lower() or "127.0.0.1" in v:
            raise ValueError("Cannot audit localhost URLs")
        
        if any(private in v.lower() for private in ["192.168.", "10.", "172.16.", "172.31."]):
            raise ValueError("Cannot audit internal network URLs")
        
        return v.lower().strip()


class AuditStatusResponse(BaseModel):
    uid: str
    status: str
    progress: int
    message: str
    results: Optional[Dict[str, Any]] = None
    created_at: str
    completed_at: Optional[str] = None


async def check_rate_limit(request: Request) -> bool:
    client_ip = request.client.host
    key = f"rate_limit:public_audit:{client_ip}"
    
    current_count = redis_client.get(key)
    
    if current_count and int(current_count) >= settings.PUBLIC_RATE_LIMIT:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "Rate limit exceeded",
                "limit": settings.PUBLIC_RATE_LIMIT,
                "window_seconds": settings.PUBLIC_RATE_LIMIT_WINDOW_SECONDS,
                "message": f"Maximum {settings.PUBLIC_RATE_LIMIT} audits per hour. Please try again later."
            }
        )
    
    return True


@router.post("/quick-audit", response_model=Dict[str, Any])
async def create_quick_audit(
    audit_request: QuickAuditRequest,
    request: Request,
    db: Session = Depends(get_db),
    rate_check: bool = Depends(check_rate_limit)
):
    try:
        audit_uid = str(uuid.uuid4())
        client_ip = request.client.host
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{settings.AUDIT_SERVICE_URL}/api/audit/public/start",
                json={
                    "url": audit_request.url,
                    "uid": audit_uid,
                    "email": audit_request.email,
                    "client_ip": client_ip,
                    "max_pages": settings.PUBLIC_AUDIT_MAX_PAGES,
                    "timeout": settings.PUBLIC_AUDIT_TIMEOUT_SECONDS
                }
            )
            
            if response.status_code != 200:
                logger.error(f"Audit Service error: {response.status_code} - {response.text}")
                raise HTTPException(
                    status_code=response.status_code,
                    detail="Failed to start audit"
                )
            
            result = response.json()
        
        key = f"rate_limit:public_audit:{client_ip}"
        pipe = redis_client.pipeline()
        pipe.incr(key)
        pipe.expire(key, settings.PUBLIC_RATE_LIMIT_WINDOW_SECONDS)
        pipe.execute()
        
        logger.info(
            f"Public audit started",
            extra={
                "uid": audit_uid,
                "url": audit_request.url,
                "client_ip": client_ip
            }
        )
        
        return {
            "success": True,
            "uid": audit_uid,
            "status": "pending",
            "message": "Audit started. Check status using the provided UID.",
            "estimated_time_seconds": 60
        }
        
    except httpx.RequestError as e:
        logger.error(f"Failed to connect to Audit Service: {e}")
        raise HTTPException(
            status_code=503,
            detail="Audit service temporarily unavailable"
        )
    except Exception as e:
        logger.error(f"Error creating audit: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/audit-status/{uid}", response_model=AuditStatusResponse)
async def get_audit_status(
    uid: str,
    db: Session = Depends(get_db)
):
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{settings.AUDIT_SERVICE_URL}/api/audit/public/status/{uid}"
            )
            
            if response.status_code == 404:
                raise HTTPException(
                    status_code=404,
                    detail="Audit not found or expired"
                )
            
            if response.status_code != 200:
                raise HTTPException(
                    status_code=response.status_code,
                    detail="Failed to fetch audit status"
                )
            
            result = response.json()
        
        return AuditStatusResponse(**result)
        
    except httpx.RequestError as e:
        logger.error(f"Failed to connect to Audit Service: {e}")
        raise HTTPException(
            status_code=503,
            detail="Audit service temporarily unavailable"
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching audit status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/rate-limit-info")
async def get_rate_limit_info(request: Request):
    client_ip = request.client.host
    key = f"rate_limit:public_audit:{client_ip}"
    
    current_count = redis_client.get(key)
    remaining = settings.PUBLIC_RATE_LIMIT - (int(current_count) if current_count else 0)
    ttl = redis_client.ttl(key)
    
    return {
        "limit": settings.PUBLIC_RATE_LIMIT,
        "remaining": max(0, remaining),
        "reset_in_seconds": ttl if ttl > 0 else settings.PUBLIC_RATE_LIMIT_WINDOW_SECONDS,
        "window_seconds": settings.PUBLIC_RATE_LIMIT_WINDOW_SECONDS
    }
