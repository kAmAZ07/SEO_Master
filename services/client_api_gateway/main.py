import hmac
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from config.logging_config import get_logger, setup_logging
from services.client_api_gateway.config import is_development, settings
from services.client_api_gateway.db import get_db, init_db
from services.client_api_gateway.db.models import DeploymentLog
from services.client_api_gateway.deployment_dispatcher import dispatch_change, rollback_change
from services.client_api_gateway.logging.changelog_logger import log_deployment
from services.client_api_gateway.routes import health_router, key_management_router, patch_router

logger = get_logger(__name__)


class InternalDeployRequest(BaseModel):
    project_id: str = Field(..., min_length=1)
    task_id: Optional[str] = None
    change_type: str = Field(..., min_length=1)
    entity_id: str = Field(..., min_length=1)
    entity_type: str = Field(..., min_length=1)
    changes: Any
    metadata: Dict[str, Any] = Field(default_factory=dict)
    correlation_id: Optional[str] = None
    status: Optional[str] = 'received'


class InternalDeployResponse(BaseModel):
    change_id: str
    deployment_id: str
    status: str


class InternalRollbackResponse(BaseModel):
    deployment_id: str
    rollback_status: str
    status: str


def _require_internal_api_key(
    x_internal_api_key: str = Header(..., alias='X-Internal-API-Key'),
):
    if not settings.INTERNAL_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail='INTERNAL_API_KEY not configured',
        )

    if not hmac.compare_digest(x_internal_api_key, settings.INTERNAL_API_KEY):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='Invalid internal API key',
        )


def _normalize_cors_list(origins: str) -> List[str]:
    if not origins:
        return ['*']
    return [origin.strip() for origin in origins.split(',') if origin.strip()]


app = FastAPI(
    title='Client API Gateway',
    description='Secure gateway for deploying client changes',
    version='1.0.0',
    docs_url='/docs' if is_development() else None,
    redoc_url='/redoc' if is_development() else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_normalize_cors_list(settings.CORS_ORIGINS),
    allow_credentials=settings.CORS_ALLOW_CREDENTIALS,
    allow_methods=[method.strip() for method in settings.CORS_ALLOW_METHODS.split(',')],
    allow_headers=[header.strip() for header in settings.CORS_ALLOW_HEADERS.split(',')],
)

app.include_router(health_router)
app.include_router(patch_router)
app.include_router(key_management_router)


@app.on_event('startup')
async def startup_event():
    setup_logging(settings.SERVICE_NAME)

    try:
        init_db()
    except Exception as exc:
        logger.error('Failed to initialize client gateway database', extra={'error': str(exc)})
        raise
    logger.info(
        f'Starting {settings.SERVICE_NAME}',
        extra={
            'environment': settings.ENVIRONMENT,
            'port': settings.SERVICE_PORT,
        },
    )


@app.on_event('shutdown')
async def shutdown_event():
    logger.info(f'Shutting down {settings.SERVICE_NAME}')


@app.middleware('http')
async def correlation_id_middleware(request: Request, call_next):
    correlation_id = request.headers.get('X-Correlation-ID', str(uuid.uuid4()))
    request.state.correlation_id = correlation_id

    response = await call_next(request)
    response.headers['X-Correlation-ID'] = correlation_id

    return response


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(
        f'Unhandled exception: {str(exc)}',
        extra={
            'path': request.url.path,
            'correlation_id': getattr(request.state, 'correlation_id', None),
        },
        exc_info=True,
    )

    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            'detail': 'Internal server error',
            'correlation_id': getattr(request.state, 'correlation_id', None),
        },
    )


@app.get('/')
async def root():
    return {
        'service': settings.SERVICE_NAME,
        'version': '1.0.0',
        'status': 'running',
        'environment': settings.ENVIRONMENT,
    }


@app.post('/internal/deploy', response_model=InternalDeployResponse)
async def internal_deploy(
    payload: InternalDeployRequest,
    request: Request,
    db: Session = Depends(get_db),
    _auth: None = Depends(_require_internal_api_key),
):
    log_entry = log_deployment(
        db=db,
        project_id=payload.project_id,
        task_id=payload.task_id,
        change_type=payload.change_type,
        entity_id=payload.entity_id,
        entity_type=payload.entity_type,
        changes=payload.changes,
        metadata=payload.metadata,
        request=request,
        correlation_id=payload.correlation_id,
        status=payload.status or 'received',
    )

    try:
        dispatch_result = await dispatch_change(
            db=db,
            change_type=payload.change_type,
            project_id=payload.project_id,
            entity_id=payload.entity_id,
            entity_type=payload.entity_type,
            changes=payload.changes,
            metadata=payload.metadata,
            correlation_id=payload.correlation_id,
        )

        dispatch_status = str(dispatch_result.get('status') or '').lower()
        if dispatch_status == 'applied':
            log_entry.status = 'applied'
            log_entry.applied_at = datetime.now(timezone.utc)
        elif dispatch_status == 'failed':
            log_entry.status = 'failed'
            log_entry.error_message = str(dispatch_result.get('error') or 'deployment_failed')
        else:
            log_entry.status = 'received'

        merged_metadata = dict(log_entry.metadata or {})
        merged_metadata['dispatch'] = dispatch_result
        log_entry.metadata = merged_metadata

        db.add(log_entry)
        db.commit()
        db.refresh(log_entry)
    except Exception as exc:
        log_entry.status = 'failed'
        log_entry.error_message = str(exc)
        db.add(log_entry)
        db.commit()

        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f'Deployment dispatch failed: {exc}',
        ) from exc

    return InternalDeployResponse(
        change_id=str(log_entry.id),
        deployment_id=str(log_entry.id),
        status=log_entry.status,
    )


@app.get('/changes/pending/{project_id}')
async def get_pending_changes(
    project_id: str,
    limit: int = 50,
    status_filter: Optional[str] = None,
    db: Session = Depends(get_db),
    _auth: None = Depends(_require_internal_api_key),
):
    query = db.query(DeploymentLog).filter(DeploymentLog.project_id == project_id)

    if status_filter:
        query = query.filter(DeploymentLog.status == status_filter)
    else:
        query = query.filter(DeploymentLog.status.in_(['received', 'pending']))

    logs = query.order_by(DeploymentLog.created_at.desc()).limit(limit).all()

    results = []
    for log in logs:
        results.append(
            {
                'change_id': str(log.id),
                'project_id': log.project_id,
                'task_id': log.task_id,
                'change_type': log.change_type,
                'entity_id': log.entity_id,
                'entity_type': log.entity_type,
                'status': log.status,
                'error_message': log.error_message,
                'created_at': log.created_at.isoformat() if log.created_at else None,
                'applied_at': log.applied_at.isoformat() if log.applied_at else None,
                'correlation_id': log.correlation_id,
            }
        )

    return results


@app.post('/internal/deploy/{deployment_id}/rollback', response_model=InternalRollbackResponse)
async def rollback_internal_deploy(
    deployment_id: str,
    request: Request,
    db: Session = Depends(get_db),
    _auth: None = Depends(_require_internal_api_key),
):
    log_entry = db.query(DeploymentLog).filter(DeploymentLog.id == deployment_id).first()
    if log_entry is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Deployment not found')

    rollback_result = await rollback_change(
        db=db,
        deployment_log=log_entry,
        correlation_id=getattr(request.state, 'correlation_id', None),
    )

    rollback_status = str(rollback_result.get('status') or 'unknown')
    if rollback_status == 'applied':
        log_entry.status = 'rolled_back'
        merged_metadata = dict(log_entry.metadata or {})
        merged_metadata['rollback'] = rollback_result
        log_entry.metadata = merged_metadata
        db.add(log_entry)
        db.commit()
    elif rollback_status == 'failed':
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail='Rollback failed')

    return InternalRollbackResponse(
        deployment_id=str(log_entry.id),
        rollback_status=rollback_status,
        status=log_entry.status,
    )


if __name__ == '__main__':
    import uvicorn

    uvicorn.run(
        'services.client_api_gateway.main:app',
        host='0.0.0.0',
        port=settings.SERVICE_PORT,
        reload=is_development(),
        log_level=settings.LOG_LEVEL.lower(),
    )
