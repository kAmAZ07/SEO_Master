from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, root_validator
from sqlalchemy.orm import Session

from services.client_api_gateway.auth import HMACAuthContext, hmac_auth
from services.client_api_gateway.db import get_db
from services.client_api_gateway.deployment_dispatcher import dispatch_change
from services.client_api_gateway.logging.changelog_logger import log_deployment

router = APIRouter(prefix='/api/client', tags=['client'])


class JsonPatchOperation(BaseModel):
    op: Literal['add', 'remove', 'replace', 'move', 'copy', 'test']
    path: str
    from_: Optional[str] = Field(None, alias='from')
    value: Optional[Any] = None

    @root_validator(pre=True)
    def validate_operation(cls, values):
        op = values.get('op')
        if op in ('add', 'replace', 'test') and 'value' not in values:
            raise ValueError(f"value is required for op '{op}'")
        if op in ('move', 'copy') and 'from' not in values and 'from_' not in values:
            raise ValueError(f"from is required for op '{op}'")
        return values

    class Config:
        allow_population_by_field_name = True


class PatchRequest(BaseModel):
    project_id: str = Field(..., min_length=1)
    task_id: Optional[str] = None
    entity_id: str = Field(..., min_length=1)
    entity_type: str = Field(..., min_length=1)
    changes: List[JsonPatchOperation] = Field(..., min_items=1)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    correlation_id: Optional[str] = None


class PatchResponse(BaseModel):
    deployment_id: str
    status: str
    change_type: str
    received_at: str


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

    changes_payload = [op.dict(by_alias=True) for op in payload.changes]

    log_entry = log_deployment(
        db=db,
        project_id=payload.project_id,
        task_id=payload.task_id,
        change_type=change_type,
        entity_id=payload.entity_id,
        entity_type=payload.entity_type,
        changes=changes_payload,
        metadata=payload.metadata,
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
