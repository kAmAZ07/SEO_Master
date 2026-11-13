from fastapi import APIRouter, Request

router = APIRouter(tags=["health"])


@router.get("/health/ready")
def readiness_check(request: Request) -> dict[str, str]:
    from fastapi import HTTPException
    from services.client_api_gateway.db import init_db
    try:
        init_db()
    except Exception:
        raise HTTPException(503, "client_schema_not_ready") from None
    from services.client_api_gateway import recovery
    task = getattr(request.app.state, "execution_recovery_task", None)
    if recovery.settings.EXECUTION_RECOVERY_ENABLED and (not recovery.configured() or task is None or task.done()):
        raise HTTPException(503, "client_recovery_not_ready")
    return {"status": "ready", "service": "client-api-gateway"}


@router.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "service": "client-api-gateway",
    }
