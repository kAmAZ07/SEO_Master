from __future__ import annotations

from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.orm import Session

from services.management_service.canonical_projects import project_exists as canonical_project_exists
from services.management_service.approval_identity import require_service_identity
from services.management_service.db.session import get_db
from services.management_service.tasks.orchestration_tasks import (
    reprioritize_all_projects,
    reprioritize_project_tasks,
    run_optimization_cycle_task,
)
from services.semantic_service.proposals.facts import ProposalRequest

router = APIRouter()


class OptimizationRunRequest(BaseModel):
    project_id: str = Field(..., min_length=1)
    url: str = Field(..., min_length=1)
    task_id: Optional[str] = None
    correlation_id: Optional[str] = None
    proposal_request: Optional[ProposalRequest] = None
    proposal_operation: Literal["title", "description", "h1", "schema_jsonld"] | None = None

    @model_validator(mode="after")
    def validate_proposal_selection(self):
        if self.proposal_request is not None and self.proposal_operation is not None:
            raise ValueError("use either proposal_request or proposal_operation")
        return self


@router.post("/optimization/run", status_code=status.HTTP_202_ACCEPTED)
def enqueue_optimization_cycle(payload: OptimizationRunRequest) -> dict[str, Any]:
    async_result = run_optimization_cycle_task.delay(
        project_id=payload.project_id,
        url=payload.url,
        task_id=payload.task_id,
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
def enqueue_project_reprioritization(project_id: str, limit: Optional[int] = None) -> dict[str, Any]:
    async_result = reprioritize_project_tasks.delay(project_id=project_id, limit=limit)
    return {
        "status": "queued",
        "celery_task_id": async_result.id,
        "project_id": project_id,
    }


@router.post("/projects/reprioritize-all", status_code=status.HTTP_202_ACCEPTED)
def enqueue_all_projects_reprioritization() -> dict[str, Any]:
    async_result = reprioritize_all_projects.delay()
    return {"status": "queued", "celery_task_id": async_result.id}


@router.get("/projects/{project_id}/exists")
def project_exists(project_id: str, db: Session = Depends(get_db),
                   _: None = Depends(require_service_identity)) -> dict[str, Any]:
    if not canonical_project_exists(db, project_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project_not_found")
    return {"project_id": project_id, "exists": True}
