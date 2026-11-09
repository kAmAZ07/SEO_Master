from __future__ import annotations

from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, UUID4, model_validator
from sqlalchemy.orm import Session

from services.management_service.canonical_projects import project_exists as canonical_project_exists
from services.management_service.approval_identity import authenticated_actor, require_service_identity
from services.management_service.approval_policy import ApprovalError, UserActor
from services.management_service.approval_service import ApprovalService
from services.management_service.task_access import accessible_task, check_target, require_editable
from shared.projects.roles import Permission
from services.management_service.db.session import get_db
from services.management_service.tasks.orchestration_tasks import (
    reprioritize_project_tasks,
    run_optimization_cycle_task,
)
from services.semantic_service.proposals.facts import ProposalRequest

router = APIRouter(dependencies=[Depends(require_service_identity)])


class OptimizationRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str = Field(..., min_length=1)
    url: str = Field(..., min_length=1)
    task_id: UUID4
    correlation_id: Optional[str] = None
    proposal_request: Optional[ProposalRequest] = None
    proposal_operation: Literal["title", "description", "h1", "schema_jsonld"] | None = None

    @model_validator(mode="after")
    def validate_proposal_selection(self):
        if self.proposal_request is not None and self.proposal_operation is not None:
            raise ValueError("use either proposal_request or proposal_operation")
        return self


@router.post("/optimization/run", status_code=status.HTTP_202_ACCEPTED)
def enqueue_optimization_cycle(payload: OptimizationRunRequest, actor: UserActor = Depends(authenticated_actor),
                               db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        task = accessible_task(db, str(payload.task_id), actor, Permission.MANAGE, lock=True)
        require_editable(db, task)
        if str(task.project_id) != payload.project_id or task.url != payload.url:
            raise ApprovalError("task_scope_mismatch")
        check_target(ApprovalService(db).project_scope(payload.project_id, actor, Permission.MANAGE), payload.url)
    except ApprovalError as exc:
        raise HTTPException(exc.http_status, exc.code) from exc
    async_result = run_optimization_cycle_task.delay(
        project_id=payload.project_id,
        url=payload.url,
        task_id=str(payload.task_id),
        correlation_id=payload.correlation_id,
        proposal_request=payload.proposal_request.model_dump(mode="json") if payload.proposal_request else None,
        proposal_operation=payload.proposal_operation,
    )
    return {
        "status": "queued",
        "celery_task_id": async_result.id,
        "project_id": payload.project_id,
        "task_id": payload.task_id,
    }


@router.post("/projects/{project_id}/reprioritize", status_code=status.HTTP_202_ACCEPTED)
def enqueue_project_reprioritization(project_id: str, limit: Optional[int] = None,
                                     actor: UserActor = Depends(authenticated_actor), db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        ApprovalService(db).project_scope(project_id, actor, Permission.MANAGE)
    except ApprovalError as exc:
        raise HTTPException(exc.http_status, exc.code) from exc
    async_result = reprioritize_project_tasks.delay(project_id=project_id, limit=limit)
    return {
        "status": "queued",
        "celery_task_id": async_result.id,
        "project_id": project_id,
    }


@router.post("/projects/reprioritize-all", status_code=status.HTTP_202_ACCEPTED)
def enqueue_all_projects_reprioritization() -> dict[str, Any]:
    raise HTTPException(410, "global_maintenance_is_not_a_user_command")


@router.get("/projects/{project_id}/exists")
def project_exists(project_id: str, db: Session = Depends(get_db),
                   _: None = Depends(require_service_identity)) -> dict[str, Any]:
    if not canonical_project_exists(db, project_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project_not_found")
    return {"project_id": project_id, "exists": True}
