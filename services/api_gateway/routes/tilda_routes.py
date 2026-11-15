"""Public official callback and authenticated read-only project controls."""
import asyncio
import logging
from typing import cast
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from database.models import User
from services.api_gateway.auth import get_current_user
from services.api_gateway.routes.protected.hitl_routes import _hitl_json
from shared.tilda_contract import CallbackAccessFilter, ManualCheckRequest, PublishHint, install_transport_redaction

router = APIRouter(prefix='/api', tags=['Tilda Beta'])
logging.getLogger('uvicorn.access').addFilter(CallbackAccessFilter())
install_transport_redaction()


def response_object(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise HTTPException(502, 'tilda_service_invalid_response')
    return cast(dict[str, object], value)


@router.get('/tilda/webhook/{binding}/{token}/published')
async def tilda_callback(binding: str, token: str, projectid: str, pageid: str, published: str,
                         publickey: str, request: Request) -> PlainTextResponse:
    try:
        hint = PublishHint(binding=binding, token=token, projectid=projectid, pageid=pageid, published=published, publickey=publickey)
    except ValueError:
        raise HTTPException(422, 'tilda_hint_invalid') from None
    # The internal endpoint commits a scoped hint; it never calls upstream Tilda.
    try:
        async with asyncio.timeout(3):
            result = await _hitl_json('POST', '/internal/tilda/webhook/published', request, payload=hint.model_dump(), execution=True, timeout=2.)
    except TimeoutError:
        raise HTTPException(503, 'tilda_hint_store_unavailable') from None
    if not isinstance(result, dict) or result.get('status') not in {'queued', 'duplicate'} or not result.get('job_id'):
        raise HTTPException(503, 'tilda_hint_not_persisted')
    return PlainTextResponse('ok', headers={'Cache-Control': 'no-store'})


@router.get('/projects/{project_id}/tilda')
async def tilda_status(project_id: UUID, request: Request, user: User = Depends(get_current_user)) -> dict[str, object]:
    return response_object(await _hitl_json('GET', f'/internal/tilda/projects/{project_id}', request, execution=True))


@router.post('/projects/{project_id}/tilda/sync')
async def tilda_sync(project_id: UUID, request: Request, user: User = Depends(get_current_user)) -> dict[str, object]:
    return response_object(await _hitl_json('POST', f'/internal/tilda/projects/{project_id}/sync', request, execution=True))


@router.get('/projects/{project_id}/tilda/pages')
async def tilda_pages(project_id: UUID, request: Request, limit: int = Query(50, ge=1, le=100),
                       offset: int = Query(0, ge=0, le=10000), user: User = Depends(get_current_user)) -> dict[str, object]:
    return response_object(await _hitl_json('GET', f'/internal/tilda/projects/{project_id}/pages', request,
        params={'limit': limit, 'offset': offset}, execution=True))


@router.post('/hitl/tasks/{task_id}/manual-verify')
async def verify_manual_change(task_id: UUID, command: ManualCheckRequest, request: Request, user: User = Depends(get_current_user)) -> dict[str, object]:
    return response_object(await _hitl_json('POST', f'/internal/tilda/tasks/{task_id}/verify', request, payload=command.model_dump(), execution=True))


@router.get('/hitl/tasks/{task_id}/manual-checks/{check_id}')
async def read_manual_check(task_id: UUID, check_id: UUID, request: Request, user: User = Depends(get_current_user)) -> dict[str, object]:
    return response_object(await _hitl_json('GET', f'/internal/tilda/tasks/{task_id}/checks/{check_id}', request, execution=True))
