from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from datetime import datetime
from typing import Dict, Any
import redis
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from config.database_config import get_db
from services.api_gateway.config import settings, get_redis_config


router = APIRouter(tags=["Health"])


def check_database(db: Session) -> Dict[str, Any]:
    try:
        result = db.execute(text("SELECT 1"))
        result.fetchone()
        return {
            "status": "healthy",
            "message": "Database connection successful"
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "message": f"Database connection failed: {str(e)}"
        }


def check_redis() -> Dict[str, Any]:
    try:
        redis_config = get_redis_config()
        r = redis.Redis(
            host=redis_config["host"],
            port=redis_config["port"],
            password=redis_config["password"],
            db=redis_config["db"],
            socket_connect_timeout=2
        )
        r.ping()
        return {
            "status": "healthy",
            "message": "Redis connection successful"
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "message": f"Redis connection failed: {str(e)}"
        }


@router.get("/health")
async def health_check(db: Session = Depends(get_db)):
    db_health = check_database(db)
    redis_health = check_redis()
    
    overall_status = "healthy"
    if db_health["status"] == "unhealthy" or redis_health["status"] == "unhealthy":
        overall_status = "unhealthy"
    
    response = {
        "status": overall_status,
        "service": settings.SERVICE_NAME,
        "version": "1.0.0",
        "timestamp": datetime.utcnow().isoformat(),
        "environment": settings.ENVIRONMENT,
        "checks": {
            "database": db_health,
            "redis": redis_health
        }
    }
    
    if overall_status == "unhealthy":
        raise HTTPException(status_code=503, detail=response)
    
    return response


@router.get("/health/live")
async def liveness_check():
    return {
        "status": "alive",
        "service": settings.SERVICE_NAME,
        "timestamp": datetime.utcnow().isoformat()
    }


@router.get("/health/ready")
async def readiness_check(db: Session = Depends(get_db)):
    db_health = check_database(db)
    redis_health = check_redis()
    
    if db_health["status"] == "unhealthy" or redis_health["status"] == "unhealthy":
        raise HTTPException(
            status_code=503,
            detail={
                "status": "not_ready",
                "database": db_health,
                "redis": redis_health
            }
        )
    
    return {
        "status": "ready",
        "service": settings.SERVICE_NAME,
        "timestamp": datetime.utcnow().isoformat()
    }
