from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from services.client_api_gateway.auth import HMACKeyConfigError, ensure_active_key, get_valid_keys, rotate_project_key
from services.client_api_gateway.config import settings
from services.client_api_gateway.db import get_db

router = APIRouter(prefix="/api/client/keys", tags=["client-keys"])


def _require_internal_api_key(
    x_internal_api_key: str = Header(..., alias="X-Internal-API-Key"),
) -> None:
    if not settings.INTERNAL_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="INTERNAL_API_KEY not configured",
        )
    if x_internal_api_key != settings.INTERNAL_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid internal API key",
        )


class ClientKeyResponse(BaseModel):
    project_id: str
    key_id: str
    is_active: bool
    secret_ref: str
    secret_fingerprint: Optional[str] = None
    created_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    grace_until: Optional[datetime] = None
    rotated_at: Optional[datetime] = None
    rotation_managed_by: str = "environment"


def _serialize_key(key) -> ClientKeyResponse:
    return ClientKeyResponse(
        project_id=key.project_id,
        key_id=key.key_id,
        is_active=bool(key.is_active),
        secret_ref=key.secret_ref,
        secret_fingerprint=(key.meta or {}).get("secret_fingerprint"),
        created_at=key.created_at,
        expires_at=key.expires_at,
        grace_until=key.grace_until,
        rotated_at=key.rotated_at,
        rotation_managed_by=(key.meta or {}).get("rotation_managed_by", "environment"),
    )


@router.get("/{project_id}", response_model=List[ClientKeyResponse])
async def list_project_keys(
    project_id: str,
    db: Session = Depends(get_db),
    _auth: None = Depends(_require_internal_api_key),
) -> List[ClientKeyResponse]:
    keys = get_valid_keys(db, project_id)
    if not keys:
        try:
            key = ensure_active_key(db, project_id)
        except HMACKeyConfigError as exc:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
        keys = [key]
    return [_serialize_key(key) for key in keys]


@router.post("/{project_id}/rotate", response_model=ClientKeyResponse)
async def rotate_key(
    project_id: str,
    db: Session = Depends(get_db),
    _auth: None = Depends(_require_internal_api_key),
) -> ClientKeyResponse:
    try:
        key = rotate_project_key(db, project_id)
    except HMACKeyConfigError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    return _serialize_key(key)
