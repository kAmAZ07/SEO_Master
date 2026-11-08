from __future__ import annotations

from typing import Any, Optional
from datetime import timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from services.management_service.db.models import HITLApproval, HITLStatus, Task
from services.management_service.db.session import get_db
from services.management_service.approval_identity import authenticated_actor
from services.management_service.approval_policy import ApprovalError, DecisionRequest, UserActor
from services.management_service.approval_service import ApprovalService
from services.management_service.config import settings

router = APIRouter()


def _service(db: Session) -> ApprovalService:
    return ApprovalService(db, ttl=timedelta(hours=settings.HITL_APPROVAL_TTL_HOURS))


def _status_from_query(value: Optional[str]) -> HITLStatus | None:
    if not value:
        return None
    normalized = value.upper()
    try:
        return HITLStatus[normalized]
    except KeyError:
        try:
            return HITLStatus(normalized)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid_hitl_status") from exc


def _serialize_approval(approval: HITLApproval, task: Optional[Task] = None) -> dict[str, Any]:
    return {
        "id": str(approval.id),
        "task_id": str(approval.task_id),
        "project_id": str(approval.project_id),
        "status": approval.status.value,
        "diff_data": approval.diff_data,
        "impact_score": approval.impact_score,
        "recommendation": approval.recommendation,
        "approved_by": approval.approved_by,
        "approved_at": approval.approved_at.isoformat() if approval.approved_at else None,
        "rejected_by": approval.rejected_by,
        "rejected_at": approval.rejected_at.isoformat() if approval.rejected_at else None,
        "rejection_reason": approval.rejection_reason,
        "metadata": approval.meta or {},
        "created_at": approval.created_at.isoformat() if approval.created_at else None,
        "updated_at": approval.updated_at.isoformat() if approval.updated_at else None,
        "task": {
            "id": str(task.id),
            "title": task.title,
            "description": task.description,
            "url": task.url,
            "task_type": task.task_type.value,
            "status": task.status.value,
            "metadata": task.meta or {},
        }
        if task
        else None,
    }


@router.get("/tasks")
def list_hitl_tasks(
    status: Optional[str] = Query(None),
    status_filter: Optional[str] = Query(None),
    project_id: Optional[str] = None,
    user_id: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    actor: UserActor = Depends(authenticated_actor),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    _ = user_id
    service = _service(db)
    projects = service.accessible_projects(actor)
    if project_id and project_id not in projects:
        raise HTTPException(404, "hitl_task_not_found")
    if not projects:
        return []
    query = db.query(HITLApproval).filter(HITLApproval.project_id.in_(projects))
    status_value = _status_from_query(status or status_filter or "pending")
    if status_value:
        query = query.filter(HITLApproval.status == status_value)
    if project_id:
        query = query.filter(HITLApproval.project_id == project_id)

    approvals = query.order_by(
        HITLApproval.impact_score.desc().nullslast(),
        HITLApproval.created_at.asc(),
    ).offset(offset).limit(limit).all()
    task_ids = [approval.task_id for approval in approvals]
    tasks_by_id = {
        task.id: task
        for task in db.query(Task).filter(Task.id.in_(task_ids)).all()
    } if task_ids else {}
    return [{**_serialize_approval(approval, tasks_by_id.get(approval.task_id)),
             "authorization_status": service.authorization_status(approval, tasks_by_id[approval.task_id], actor)}
            for approval in approvals if approval.task_id in tasks_by_id]


@router.get("/tasks/{task_id}")
def get_hitl_task(task_id: UUID, actor: UserActor = Depends(authenticated_actor),
                  db: Session = Depends(get_db)) -> dict[str, Any]:
    service = _service(db)
    try:
        approval, task = service.readable(str(task_id), actor)
        return {**_serialize_approval(approval, task),
                "authorization_status": service.authorization_status(approval, task, actor)}
    except ApprovalError as exc:
        raise HTTPException(exc.http_status, exc.code) from exc


@router.post("/tasks/{task_id}/approve")
def approve_hitl_task(
    task_id: UUID,
    payload: DecisionRequest,
    actor: UserActor = Depends(authenticated_actor),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        return _service(db).decide(str(task_id), actor, payload, "approve")
    except ApprovalError as exc:
        raise HTTPException(exc.http_status, exc.code) from exc


@router.get("/tasks/{task_id}/history")
def get_hitl_history(task_id: UUID, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0, le=100_000),
                      actor: UserActor = Depends(authenticated_actor), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    try:
        return _service(db).history(str(task_id), actor, limit=limit, offset=offset)
    except ApprovalError as exc:
        raise HTTPException(exc.http_status, exc.code) from exc


@router.post("/tasks/{task_id}/reject")
def reject_hitl_task(
    task_id: UUID,
    payload: DecisionRequest,
    actor: UserActor = Depends(authenticated_actor),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        return _service(db).decide(str(task_id), actor, payload, "reject")
    except ApprovalError as exc:
        raise HTTPException(exc.http_status, exc.code) from exc


@router.post("/tasks/{task_id}/revoke")
def revoke_hitl_task(task_id: UUID, payload: DecisionRequest,
                     actor: UserActor = Depends(authenticated_actor),
                     db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        return _service(db).decide(str(task_id), actor, payload, "revoke")
    except ApprovalError as exc:
        raise HTTPException(exc.http_status, exc.code) from exc
