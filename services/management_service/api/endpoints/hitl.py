from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from services.management_service.db.models import HITLApproval, HITLStatus, Task
from services.management_service.db.session import get_db
from services.management_service.hitl_handler import HITLHandler
from services.management_service.schemas.hitl import HITLDecision

router = APIRouter()


class HITLDecisionRequest(BaseModel):
    user_id: str = Field(..., min_length=1)
    comment: Optional[str] = None
    notes: Optional[str] = None
    rejection_reason: Optional[str] = None
    auto_deploy: bool = True


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
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    _ = user_id
    query = db.query(HITLApproval)
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
    return [_serialize_approval(approval, tasks_by_id.get(approval.task_id)) for approval in approvals]


@router.get("/tasks/{task_id}")
def get_hitl_task(task_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    handler = HITLHandler(db)
    result = handler.get_approval_with_task(task_id)
    if not result:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="hitl_task_not_found")
    return _serialize_approval(result["approval"], result["task"])


@router.post("/tasks/{task_id}/approve")
async def approve_hitl_task(
    task_id: str,
    payload: HITLDecisionRequest,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    handler = HITLHandler(db)
    try:
        return await handler.approve_task(
            task_id=task_id,
            approved_by=payload.user_id,
            decision=HITLDecision(
                auto_deploy=payload.auto_deploy,
                notes=payload.notes or payload.comment,
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/tasks/{task_id}/reject")
def reject_hitl_task(
    task_id: str,
    payload: HITLDecisionRequest,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    handler = HITLHandler(db)
    try:
        return handler.reject_task(
            task_id=task_id,
            rejected_by=payload.user_id,
            decision=HITLDecision(
                notes=payload.notes or payload.comment,
                rejection_reason=payload.rejection_reason or payload.comment,
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
