from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health/ready")
def readiness_check():
    from fastapi import HTTPException
    from services.client_api_gateway.db import init_db
    try:
        init_db()
    except Exception:
        raise HTTPException(503, "client_schema_not_ready") from None
    return {"status": "ready", "service": "client-api-gateway"}


@router.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "service": "client-api-gateway",
    }
