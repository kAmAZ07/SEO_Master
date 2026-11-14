from typing import Any, Dict, List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from config.database_config import get_db
from config.logging_config import get_logger
from database.models import (
    User,
)
from services.api_gateway.config import settings
from shared.projects.roles import Permission
from shared.hitl_execution import ExecutionRequest
from shared.hitl_rollback import RollbackRequest
from services.api_gateway.auth import (
    get_current_user,
)

from services.api_gateway.routes.protected.common import (
    _get_owned_project,
    _get_owned_projects,
    _project_ids,
)

logger = get_logger(__name__)
router = APIRouter(prefix="/api", tags=["Protected"])


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    approval_id: str = Field(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
    recommendation_version_id: str = Field(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    comment: Optional[str] = Field(default=None, max_length=1000)


async def _hitl_json(method: str, path: str, request: Request, *, params=None, payload=None, execution=False):
    if len(settings.INTERNAL_API_KEY) < 32:
        raise HTTPException(503, "service_identity_unconfigured")
    authorization = request.headers.get("authorization", "")
    try:
        async with httpx.AsyncClient(timeout=50 if execution else 10, trust_env=False, follow_redirects=False) as client:
            response = await client.request(
                method, (settings.CLIENT_GATEWAY_URL if execution else settings.MANAGEMENT_SERVICE_URL).rstrip("/") + path,
                params=params, json=payload,
                headers={"Authorization": authorization, "X-Internal-API-Key": settings.INTERNAL_API_KEY},
            )
    except httpx.RequestError:
        raise HTTPException(503, "hitl_service_unavailable") from None
    try:
        data = response.json()
    except ValueError:
        raise HTTPException(502, "hitl_service_invalid_response") from None
    if response.status_code >= 400:
        detail = data.get("detail", "hitl_request_failed") if isinstance(data, dict) else "hitl_request_failed"
        raise HTTPException(response.status_code, detail)
    return data


@router.post("/hitl/tasks/{task_id}/apply")
async def apply_task(task_id: str, body: ExecutionRequest, request: Request,
                     current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if body.task_id != task_id:
        raise HTTPException(409, "execution_task_mismatch")
    await _get_owned_hitl_task(task_id, current_user, db, request, Permission.DEPLOY)
    return await _hitl_json("POST", "/internal/executions", request, payload=body.model_dump(), execution=True)


@router.get("/hitl/tasks/{task_id}/executions/{operation_id}")
async def execution_status(task_id: str, operation_id: str, request: Request,
                           current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from uuid import UUID
    try:
        UUID(operation_id)
    except ValueError:
        raise HTTPException(422, "execution_id_invalid") from None
    await _get_owned_hitl_task(task_id, current_user, db, request)
    data = await _hitl_json("GET", "/internal/executions/" + operation_id, request, execution=True)
    if not isinstance(data, dict) or data.get("record", {}).get("task_id") != task_id:
        raise HTTPException(404, "execution_not_found")
    return data


@router.post("/hitl/tasks/{task_id}/executions/{operation_id}/cancel")
async def cancel_execution(task_id: str, operation_id: str, request: Request,
                           current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    await _get_owned_hitl_task(task_id, current_user, db, request, Permission.DEPLOY)
    # Task identity is immutable, so this scoped read remains valid through cancel.
    await execution_status(task_id, operation_id, request, current_user, db)
    data = await _hitl_json("POST", "/internal/executions/" + operation_id + "/cancel", request, execution=True)
    if not isinstance(data, dict):
        raise HTTPException(502, "hitl_service_invalid_response")
    return data


@router.post("/hitl/tasks/{task_id}/executions/{operation_id}/rollback")
async def rollback_execution(task_id: str, operation_id: str, body: RollbackRequest, request: Request,
                             current_user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    await _get_owned_hitl_task(task_id, current_user, db, request, Permission.DEPLOY)
    await execution_status(task_id, operation_id, request, current_user, db)
    data = await _hitl_json("POST", "/internal/executions/" + operation_id + "/rollback", request,
                            payload=body.model_dump(), execution=True)
    if not isinstance(data, dict):
        raise HTTPException(502, "hitl_service_invalid_response")
    return data


async def _get_owned_hitl_task(
    task_id: str,
    current_user: User,
    db: Session,
    request: Request,
    permission: Permission = Permission.READ,
) -> Dict[str, Any]:
    data = await _hitl_json(
        "GET",
        f"/api/v1/hitl/tasks/{task_id}", request,
    )
    if not isinstance(data, dict):
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="hitl_task_invalid_response")

    project_id = str(data.get("project_id") or data.get("projectId") or "")
    if not project_id:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="hitl_task_missing_project")
    _get_owned_project(project_id, current_user, db, not_found_detail="hitl_task_not_found", permission=permission)
    return data


@router.get("/tasks")
async def get_management_tasks(
    request: Request,
    project_id: Optional[str] = Query(None, alias="project_id"),
    status_filter: Optional[str] = Query(None, alias="status"),
    limit: int = Query(50, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if project_id:
        project_ids = [str(_get_owned_project(project_id, current_user, db).id)]
    else:
        project_ids = _project_ids(_get_owned_projects(current_user, db))

    items: List[Dict[str, Any]] = []
    for owned_project_id in project_ids:
        data = await _hitl_json(
            "GET",
            "/api/v1/tasks", request,
            params={
                "project_id": owned_project_id,
                "status": status_filter,
                "limit": limit,
            },
        )
        if isinstance(data, list):
            items.extend(item for item in data if isinstance(item, dict))

    return sorted(
        items,
        key=lambda item: item.get("updated_at") or item.get("created_at") or "",
        reverse=True,
    )[:limit]


@router.get("/hitl/tasks")
async def get_hitl_tasks(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    status_filter: Optional[str] = "pending",
    limit: int = 50,
):
    items: List[Dict[str, Any]] = []
    project_ids = _project_ids(_get_owned_projects(current_user, db))
    for project_id in project_ids:
        data = await _hitl_json(
            "GET",
            "/api/v1/hitl/tasks", request,
            params={"status": status_filter, "limit": limit, "project_id": project_id},
        )
        if isinstance(data, list):
            items.extend(item for item in data if isinstance(item, dict))

    return sorted(items, key=lambda item: item.get("created_at") or item.get("createdAt") or "", reverse=True)[:limit]


@router.get("/hitl/tasks/{task_id}")
async def get_hitl_task_details(
    task_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return await _get_owned_hitl_task(task_id, current_user, db, request)


@router.post("/hitl/tasks/{task_id}/approve")
async def approve_task(
    task_id: str,
    request: ApprovalRequest,
    http_request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    await _get_owned_hitl_task(task_id, current_user, db, http_request, Permission.APPROVE)
    return await _hitl_json("POST", f"/api/v1/hitl/tasks/{task_id}/approve", http_request,
                            payload=request.model_dump())


@router.get("/hitl/tasks/{task_id}/history")
async def get_hitl_history(task_id: str, request: Request, limit: int = Query(50, ge=1, le=100),
                           offset: int = Query(0, ge=0, le=100_000),
                           current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    await _get_owned_hitl_task(task_id, current_user, db, request)
    return await _hitl_json("GET", f"/api/v1/hitl/tasks/{task_id}/history", request,
                            params={"limit": limit, "offset": offset})


@router.post("/hitl/tasks/{task_id}/reject")
async def reject_task(
    task_id: str,
    request: ApprovalRequest,
    http_request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    await _get_owned_hitl_task(task_id, current_user, db, http_request, Permission.APPROVE)
    return await _hitl_json("POST", f"/api/v1/hitl/tasks/{task_id}/reject", http_request,
                            payload=request.model_dump())


@router.post("/hitl/tasks/{task_id}/revoke")
async def revoke_task(task_id: str, request: ApprovalRequest, http_request: Request,
                      current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    await _get_owned_hitl_task(task_id, current_user, db, http_request, Permission.APPROVE)
    return await _hitl_json("POST", f"/api/v1/hitl/tasks/{task_id}/revoke", http_request,
                            payload=request.model_dump())


@router.post("/hitl/approve/{task_id}")
async def approve_task_legacy(
    task_id: str,
    request: ApprovalRequest,
    http_request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return await approve_task(task_id=task_id, request=request, http_request=http_request,
                              current_user=current_user, db=db)


@router.post("/hitl/reject/{task_id}")
async def reject_task_legacy(
    task_id: str,
    request: ApprovalRequest,
    http_request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return await reject_task(task_id=task_id, request=request, http_request=http_request,
                             current_user=current_user, db=db)
