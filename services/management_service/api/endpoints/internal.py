from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from services.management_service.db.models import Project
from services.management_service.db.session import get_db
from services.management_service.tasks.orchestration_tasks import (
    reprioritize_all_projects,
    reprioritize_project_tasks,
    run_optimization_cycle_task,
)

router = APIRouter()


class OptimizationRunRequest(BaseModel):
    project_id: str = Field(..., min_length=1)
    url: str = Field(..., min_length=1)
    task_id: Optional[str] = None
    correlation_id: Optional[str] = None


@router.post("/optimization/run", status_code=status.HTTP_202_ACCEPTED)
def enqueue_optimization_cycle(payload: OptimizationRunRequest) -> dict[str, Any]:
    async_result = run_optimization_cycle_task.delay(
        project_id=payload.project_id,
        url=payload.url,
        task_id=payload.task_id,
        correlation_id=payload.correlation_id,
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
def project_exists(project_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    project = db.query(Project.id).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project_not_found")
    return {"project_id": project_id, "exists": True}
