"""Authenticated read transport; legacy mutation routes always report unsupported."""
import hmac
from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from config import settings
from shared.tilda_contract import TildaReadRequest, capabilities, unsupported
from tilda_api_client import TildaAPIClient, TildaAPIError

router = APIRouter(prefix='/internal/tilda', tags=['internal'])


def require_internal_key(x_internal_api_key: str | None = Header(default=None)) -> None:
    if not settings.internal_api_key or len(settings.internal_api_key) < 32:
        raise HTTPException(503, 'tilda_service_identity_unconfigured')
    if x_internal_api_key is None or not hmac.compare_digest(x_internal_api_key.encode(), settings.internal_api_key.encode()):
        raise HTTPException(401, 'tilda_service_identity_required')


class PatchOp(BaseModel):
    model_config = ConfigDict(extra='forbid')
    op: Literal['add', 'replace', 'remove', 'move', 'copy', 'test']
    path: str = Field(max_length=2048)
    value: object = None


class TildaPatchRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    project_id: str = Field(min_length=1, max_length=255)
    page_id: str = Field(min_length=1, max_length=255)
    changes: list[PatchOp] = Field(default_factory=list, max_length=100)
    metadata: dict[str, object] = Field(default_factory=dict)
    credentials: dict[str, str] = Field(default_factory=dict)


@router.get('/capabilities', dependencies=[Depends(require_internal_key)])
def read_capabilities() -> dict[str, object]:
    return capabilities()


@router.post('/read', dependencies=[Depends(require_internal_key)])
async def read_source(payload: TildaReadRequest) -> dict[str, object]:
    try:
        client = TildaAPIClient(public_key=payload.public_key.get_secret_value(), secret_key=payload.secret_key.get_secret_value())
        result = await client.read(payload.method, project_id=payload.external_project_id, page_id=payload.page_id)
        return {'status': 'FOUND', 'result': result}
    except TildaAPIError as error:
        status = 429 if error.code == 'tilda_quota_exceeded' else 503 if error.code == 'tilda_read_disabled' else 502
        raise HTTPException(status, error.code, headers={'Retry-After': str(error.retry_after)} if error.retry_after else None) from None


@router.post('/meta', dependencies=[Depends(require_internal_key)])
@router.post('/schema', dependencies=[Depends(require_internal_key)])
@router.post('/interlinks', dependencies=[Depends(require_internal_key)])
async def reject_write(payload: TildaPatchRequest) -> dict[str, object]:
    return unsupported(payload.page_id)
