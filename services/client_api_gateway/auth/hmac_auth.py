from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from fastapi import Header, HTTPException, Request, Depends, status
from sqlalchemy.orm import Session

from services.client_api_gateway.auth.signature_validator import (
    validate_request_signature,
    SignatureValidationError,
)
from services.client_api_gateway.db import get_db


@dataclass
class HMACAuthContext:
    project_id: str
    key_id: Optional[str]


def _build_path_with_query(request: Request) -> str:
    path = request.url.path
    if request.url.query:
        path = f"{path}?{request.url.query}"
    return path


async def hmac_auth(
    request: Request,
    x_project_id: str = Header(..., alias="X-Project-ID"),
    x_signature: str = Header(..., alias="X-Signature"),
    x_timestamp: str = Header(..., alias="X-Timestamp"),
    x_key_id: Optional[str] = Header(None, alias="X-Key-ID"),
    db: Session = Depends(get_db),
) -> HMACAuthContext:
    body = await request.body()
    path = _build_path_with_query(request)

    try:
        key = validate_request_signature(
            db=db,
            project_id=x_project_id,
            signature=x_signature,
            timestamp=x_timestamp,
            method=request.method,
            path=path,
            body=body,
            key_id=x_key_id,
        )
    except SignatureValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
        ) from exc

    if key:
        key.last_used_at = datetime.now(timezone.utc)
        db.add(key)
        db.commit()

    return HMACAuthContext(project_id=x_project_id, key_id=key.key_id if key else x_key_id)
