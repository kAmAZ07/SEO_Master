from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy.orm import Session

from services.client_api_gateway.auth import HMACAuthContext, hmac_auth
from services.client_api_gateway.db import get_db

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
    raise HTTPException(409, "version_bound_execution_required")


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
