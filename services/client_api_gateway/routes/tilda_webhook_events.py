"""Durable hints are independent of CMS commands, approvals and ownership."""
import hmac
from typing import Any, Literal, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
import httpx
from sqlalchemy.orm import Session

from services.client_api_gateway.config import settings
from services.client_api_gateway.db import get_db
from services.client_api_gateway import tilda_sync
from services.client_api_gateway import tilda_worker
from shared.projects.roles import Permission, has_permission
from shared.tilda_contract import ManualCheckRequest, PublishHint, capabilities
from services.client_api_gateway.tilda_manual import stored_check, verify_manual

router = APIRouter(prefix='/internal/tilda', tags=['tilda'])


def require_service(x_internal_api_key: str | None = Header(default=None)) -> None:
    key = settings.INTERNAL_API_KEY or ''
    if len(key) < 32:
        raise HTTPException(503, 'service_identity_unconfigured')
    if x_internal_api_key is None or not hmac.compare_digest(x_internal_api_key.encode(), key.encode()):
        raise HTTPException(401, 'service_identity_required')


async def project_scope(project_id: str, request: Request, permission: Literal['read', 'manage']) -> dict[str, Any]:
    authorization = request.headers.get('authorization', '')
    if not authorization.lower().startswith('bearer ') or len(authorization) > 8192:
        raise HTTPException(401, 'user_session_required')
    try:
        async with httpx.AsyncClient(timeout=8, trust_env=False, follow_redirects=False) as client:
            response = await client.get(settings.MANAGEMENT_SERVICE_URL.rstrip('/') + f'/api/v1/hitl/projects/{project_id}/scope',
                params={'permission': permission}, headers={'Authorization': authorization, 'X-Internal-API-Key': settings.INTERNAL_API_KEY or ''})
    except httpx.HTTPError:
        raise HTTPException(503, 'project_authority_unavailable') from None
    if response.status_code != 200:
        raise HTTPException(response.status_code if response.status_code in {401, 403, 404, 409} else 503, 'project_access_denied')
    try:
        scope = response.json()
        if (not isinstance(scope, dict) or scope['id'] != project_id or scope['status'] != 'active' or not scope['actor_id']
                or not has_permission(str(scope.get('role')), Permission(permission))):
            raise ValueError()
    except (ValueError, TypeError, KeyError):
        raise HTTPException(503, 'project_authority_invalid_response') from None
    return cast(dict[str, Any], scope)


def fail(error: tilda_sync.TildaSyncError) -> HTTPException:
    return HTTPException(error.status, error.code,
        headers={'Retry-After': str(error.retry_after)} if error.retry_after else None)


def reader_active(request: Request) -> bool:
    task = getattr(request.app.state, 'tilda_read_task', None)
    return bool(tilda_worker.configured() and task is not None and not task.done())


@router.post('/webhook/published', dependencies=[Depends(require_service)])
def record_hint(payload: PublishHint, request: Request, db: Session = Depends(get_db)) -> dict[str, str]:
    if not reader_active(request):
        raise HTTPException(503, 'tilda_reader_not_ready')
    try:
        return tilda_sync.enqueue_hint(db, payload)
    except tilda_sync.TildaSyncError as error:
        raise fail(error) from None


@router.get('/projects/{project_id}', dependencies=[Depends(require_service)])
async def source_status(project_id: UUID, request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    scope = await project_scope(str(project_id), request, 'read')
    try:
        credentials = tilda_sync.binding(db, str(project_id))
        callback = tilda_sync.callback_url(str(project_id), credentials) if has_permission(scope['role'], Permission.MANAGE) else None
        return {'project_id': str(project_id), 'capabilities': {**capabilities(),
                    'configured_requests_per_hour': settings.TILDA_REQUESTS_PER_HOUR}, 'callback_url': callback,
                'reader_state': 'active' if reader_active(request) else 'unavailable' if settings.TILDA_SYNC_ENABLED else 'disabled',
                'jobs': tilda_sync.cached_jobs(db, str(project_id)),
                'can_sync': reader_active(request) and has_permission(scope['role'], Permission.MANAGE)}
    except tilda_sync.TildaSyncError as error:
        raise fail(error) from None


@router.post('/projects/{project_id}/sync', dependencies=[Depends(require_service)])
async def synchronize_source(project_id: UUID, request: Request, db: Session = Depends(get_db)) -> dict[str, str]:
    scope = await project_scope(str(project_id), request, 'manage')
    if not reader_active(request):
        raise HTTPException(503, 'tilda_reader_not_ready')
    try:
        return tilda_sync.enqueue_catalogue(db, str(project_id), scope['actor_id'])
    except tilda_sync.TildaSyncError as error:
        db.rollback()
        raise fail(error) from None


@router.get('/projects/{project_id}/pages', dependencies=[Depends(require_service)])
async def page_directory(project_id: UUID, request: Request, limit: int = Query(50, ge=1, le=100),
                          offset: int = Query(0, ge=0, le=10000), db: Session = Depends(get_db)) -> dict[str, Any]:
    await project_scope(str(project_id), request, 'read')
    try:
        return tilda_sync.cached_pages(db, str(project_id), limit, offset)
    except tilda_sync.TildaSyncError as error:
        raise fail(error) from None


async def approval_snapshot(task_id: str, request: Request) -> dict[str, Any]:
    try:
        async with httpx.AsyncClient(timeout=8, trust_env=False, follow_redirects=False) as client:
            response = await client.get(settings.MANAGEMENT_SERVICE_URL.rstrip('/') + f'/api/v1/hitl/tasks/{task_id}',
                headers={'Authorization': request.headers.get('authorization', ''), 'X-Internal-API-Key': settings.INTERNAL_API_KEY or ''})
    except httpx.HTTPError:
        raise tilda_sync.TildaSyncError('manual_authority_unavailable', 503) from None
    if response.status_code != 200:
        raise tilda_sync.TildaSyncError('manual_authority_denied', response.status_code if response.status_code in {401, 403, 404} else 503)
    try:
        data = response.json()
        if not isinstance(data, dict) or data['task_id'] != task_id or not data['project_id']:
            raise ValueError()
        return cast(dict[str, Any], data)
    except (ValueError, TypeError, KeyError):
        raise tilda_sync.TildaSyncError('manual_authority_invalid_response', 503) from None


@router.post('/tasks/{task_id}/verify', dependencies=[Depends(require_service)])
async def check_manual_implementation(task_id: UUID, command: ManualCheckRequest, request: Request,
                                      db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        snapshot = await approval_snapshot(str(task_id), request)
        scope = await project_scope(snapshot['project_id'], request, 'read')
        async def authority() -> dict[str, Any]:
            return await approval_snapshot(str(task_id), request)
        return await verify_manual(db, snapshot['project_id'], str(task_id), scope['actor_id'], command, authority)
    except tilda_sync.TildaSyncError as error:
        db.rollback()
        raise fail(error) from None


@router.get('/tasks/{task_id}/checks/{check_id}', dependencies=[Depends(require_service)])
async def manual_check_status(task_id: UUID, check_id: UUID, request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        snapshot = await approval_snapshot(str(task_id), request)
        return stored_check(db, snapshot['project_id'], str(task_id), str(check_id))
    except tilda_sync.TildaSyncError as error:
        raise fail(error) from None
