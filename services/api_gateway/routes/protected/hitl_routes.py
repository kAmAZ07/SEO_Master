import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from config.database_config import get_db
from config.logging_config import get_logger
from database.models import (
    User,
)
from services.api_gateway.events.hitl_approved import publish_hitl_approved_event
from services.api_gateway.auth import (
    get_current_user,
)

from services.api_gateway.routes.protected.common import (
    _get_owned_project,
    _get_owned_projects,
    _project_ids,
    _proxy_management_required_json,
    _proxy_management_response,
)

logger = get_logger(__name__)
router = APIRouter(prefix="/api", tags=["Protected"])


class ApprovalRequest(BaseModel):
    comment: Optional[str] = None


async def _get_owned_hitl_task(
    task_id: str,
    current_user: User,
    db: Session,
) -> Dict[str, Any]:
    data = await _proxy_management_required_json(
        "GET",
        [f"/api/v1/hitl/tasks/{task_id}", f"/api/hitl/tasks/{task_id}"],
        unavailable_detail="hitl_task_unavailable",
    )
    if not isinstance(data, dict):
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="hitl_task_invalid_response")

    project_id = str(data.get("project_id") or data.get("projectId") or "")
    if not project_id:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="hitl_task_missing_project")
    _get_owned_project(project_id, current_user, db, not_found_detail="hitl_task_not_found")
    return data


@router.get("/tasks")
async def get_management_tasks(
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
        data = await _proxy_management_required_json(
            "GET",
            ["/api/v1/tasks"],
            params={
                "project_id": owned_project_id,
                "status": status_filter,
                "limit": limit,
            },
            unavailable_detail="management_tasks_unavailable",
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
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    status_filter: Optional[str] = "pending",
    limit: int = 50,
):
    items: List[Dict[str, Any]] = []
    project_ids = _project_ids(_get_owned_projects(current_user, db))
    for project_id in project_ids:
        data = await _proxy_management_required_json(
            "GET",
            ["/api/v1/hitl/tasks", "/api/hitl/tasks"],
            params={"status": status_filter, "limit": limit, "project_id": project_id},
            unavailable_detail="hitl_tasks_unavailable",
        )
        if isinstance(data, list):
            items.extend(item for item in data if isinstance(item, dict))

    return sorted(items, key=lambda item: item.get("created_at") or item.get("createdAt") or "", reverse=True)[:limit]


@router.get("/hitl/tasks/{task_id}")
async def get_hitl_task_details(
    task_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return await _get_owned_hitl_task(task_id, current_user, db)


@router.post("/hitl/tasks/{task_id}/approve")
async def approve_task(
    task_id: str,
    request: ApprovalRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    await _get_owned_hitl_task(task_id, current_user, db)
    correlation_id = str(uuid.uuid4())
    payload = {"user_id": str(current_user.id), "comment": request.comment}
    response = await _proxy_management_response(
        "POST",
        [f"/api/v1/hitl/tasks/{task_id}/approve", f"/api/hitl/approve/{task_id}"],
        payload=payload,
    )
    if response is None:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="hitl_approval_unavailable")
    if response.status_code >= 400:
        detail = None
        try:
            detail = response.json().get("detail")
        except ValueError:
            detail = response.text[:300] or "hitl_approval_failed"
        raise HTTPException(status_code=response.status_code, detail=detail)
    data = response.json() if response.content else {}

    project_id = data.get("project_id")
    approved_at = data.get("approved_at")
    approved_by = data.get("approved_by") or str(current_user.id)
    if not project_id or not approved_at:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="hitl_approval_missing_context")

    try:
        await publish_hitl_approved_event(
            task_id=task_id,
            project_id=str(project_id),
            approved_by=approved_by,
            approved_at=str(approved_at),
            auto_deployed=bool(data.get("auto_deployed", True)),
            notes=request.comment,
            correlation_id=correlation_id,
        )
    except Exception as exc:
        logger.error(
            "HITL approval succeeded but event publication failed",
            extra={"task_id": task_id, "project_id": project_id, "correlation_id": correlation_id},
            exc_info=True,
        )
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="hitl_approval_event_unavailable") from exc

    return {
        **data,
        "event_published": True,
        "correlation_id": correlation_id,
    }


@router.post("/hitl/tasks/{task_id}/reject")
async def reject_task(
    task_id: str,
    request: ApprovalRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    await _get_owned_hitl_task(task_id, current_user, db)
    payload = {"user_id": str(current_user.id), "comment": request.comment}
    response = await _proxy_management_response(
        "POST",
        [f"/api/v1/hitl/tasks/{task_id}/reject", f"/api/hitl/reject/{task_id}"],
        payload=payload,
    )
    if response is None:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="hitl_rejection_unavailable")
    if response.status_code >= 400:
        detail = None
        try:
            detail = response.json().get("detail")
        except ValueError:
            detail = response.text[:300] or "hitl_rejection_failed"
        raise HTTPException(status_code=response.status_code, detail=detail)
    data = response.json() if response.content else {}
    return data


@router.post("/hitl/approve/{task_id}")
async def approve_task_legacy(
    task_id: str,
    request: ApprovalRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return await approve_task(task_id=task_id, request=request, current_user=current_user, db=db)


@router.post("/hitl/reject/{task_id}")
async def reject_task_legacy(
    task_id: str,
    request: ApprovalRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return await reject_task(task_id=task_id, request=request, current_user=current_user, db=db)
