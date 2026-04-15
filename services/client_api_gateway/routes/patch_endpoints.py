from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.orm import Session

from services.client_api_gateway.auth import HMACAuthContext, hmac_auth
from services.client_api_gateway.auth.access_control import enforce_project_rate_limit, get_client_ip
from services.client_api_gateway.db import get_db
from services.client_api_gateway.deployment_dispatcher import dispatch_change
from services.client_api_gateway.logging.changelog_logger import log_deployment

router = APIRouter(prefix='/api/client', tags=['client'])


class JsonPatchOperation(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    op: Literal['add', 'remove', 'replace', 'move', 'copy', 'test']
    path: str
    from_: Optional[str] = Field(None, alias='from')
    value: Optional[Any] = None

    @model_validator(mode='before')
    @classmethod
    def validate_operation(cls, values):
        op = values.get('op')
        if op in ('add', 'replace', 'test') and 'value' not in values:
            raise ValueError(f"value is required for op '{op}'")
        if op in ('move', 'copy') and 'from' not in values and 'from_' not in values:
            raise ValueError(f"from is required for op '{op}'")
        return values


class PatchRequest(BaseModel):
    project_id: str = Field(..., min_length=1)
    task_id: Optional[str] = None
    entity_id: str = Field(..., min_length=1)
    entity_type: str = Field(..., min_length=1)
    changes: List[JsonPatchOperation] = Field(..., min_length=1)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    correlation_id: Optional[str] = None


class PatchResponse(BaseModel):
    deployment_id: str
    status: str
    change_type: str
    received_at: str
    warnings: List[Dict[str, Any]] = Field(default_factory=list)


async def _handle_patch(
    request: Request,
    payload: PatchRequest,
    change_type: str,
    db: Session,
    auth_ctx: HMACAuthContext,
) -> PatchResponse:
    if payload.project_id != auth_ctx.project_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail='Project ID mismatch',
        )

    rate_limit = enforce_project_rate_limit(db, payload.project_id)
    changes_payload = [op.model_dump(by_alias=True) for op in payload.changes]
    audit_metadata = {
        **payload.metadata,
        'patch_audit': {
            'method': request.method,
            'path': request.url.path,
            'change_type': change_type,
            'auth_key_id': auth_ctx.key_id,
            'client_ip': get_client_ip(request),
            'user_agent': request.headers.get('user-agent'),
            'rate_limit': rate_limit,
            'received_at': datetime.now(timezone.utc).isoformat(),
        },
    }

    log_entry = log_deployment(
        db=db,
        project_id=payload.project_id,
        task_id=payload.task_id,
        change_type=change_type,
        entity_id=payload.entity_id,
        entity_type=payload.entity_type,
        changes=changes_payload,
        metadata=audit_metadata,
        request=request,
        correlation_id=payload.correlation_id,
        status='received',
    )

    try:
        dispatch_result = await dispatch_change(
            db=db,
            change_type=change_type,
            project_id=payload.project_id,
            entity_id=payload.entity_id,
            entity_type=payload.entity_type,
            changes=changes_payload,
            metadata=audit_metadata,
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

        merged_metadata = dict(log_entry.meta or {})
        merged_metadata['dispatch'] = dispatch_result
        log_entry.meta = merged_metadata

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

    return PatchResponse(
        deployment_id=str(log_entry.id),
        status=log_entry.status,
        change_type=change_type,
        received_at=log_entry.created_at.isoformat(),
    )


@router.patch('/meta', response_model=PatchResponse)
async def patch_meta(
    payload: PatchRequest,
    request: Request,
    db: Session = Depends(get_db),
    auth_ctx: HMACAuthContext = Depends(hmac_auth),
) -> PatchResponse:
    return await _handle_patch(request, payload, 'meta', db, auth_ctx)


@router.patch('/schema', response_model=PatchResponse)
async def patch_schema(
    payload: PatchRequest,
    request: Request,
    db: Session = Depends(get_db),
    auth_ctx: HMACAuthContext = Depends(hmac_auth),
) -> PatchResponse:
    return await _handle_patch(request, payload, 'schema', db, auth_ctx)


@router.patch('/interlinks', response_model=PatchResponse)
async def patch_interlinks(
    payload: PatchRequest,
    request: Request,
    db: Session = Depends(get_db),
    auth_ctx: HMACAuthContext = Depends(hmac_auth),
) -> PatchResponse:
    return await _handle_patch(request, payload, 'interlinks', db, auth_ctx)
