from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from services.management_service.db.models import Task, TaskStatus, TaskType
from services.management_service.db.session import get_db
from services.management_service.events.task_created import publish_task_created_event
from services.management_service.prioritizer import prioritize_project_tasks
from services.management_service.schemas.task import TaskCreate, TaskUpdate

router = APIRouter()


def _status_from_query(value: Optional[str]) -> TaskStatus | None:
    if not value:
        return None
    normalized = value.upper()
    try:
        return TaskStatus[normalized]
    except KeyError:
        try:
            return TaskStatus(normalized)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="invalid_task_status") from exc


def _serialize_task(task: Task) -> dict[str, Any]:
    return {
        "id": str(task.id),
        "project_id": str(task.project_id),
        "task_type": task.task_type.value,
        "status": task.status.value,
        "url": task.url,
        "title": task.title,
        "description": task.description,
        "impact_score": task.impact_score,
        "effort_score": task.effort_score,
        "priority_score": task.priority_score,
        "metadata": task.meta or {},
        "assigned_to": task.assigned_to,
        "created_at": task.created_at.isoformat() if task.created_at else None,
        "updated_at": task.updated_at.isoformat() if task.updated_at else None,
        "started_at": task.started_at.isoformat() if task.started_at else None,
        "completed_at": task.completed_at.isoformat() if task.completed_at else None,
        "deployed_at": task.deployed_at.isoformat() if task.deployed_at else None,
    }


@router.get("")
@router.get("/")
def list_tasks(
    project_id: Optional[str] = None,
    status_filter: Optional[str] = Query(None, alias="status"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    query = db.query(Task)
    if project_id:
        query = query.filter(Task.project_id == project_id)
    status_value = _status_from_query(status_filter)
    if status_value:
        query = query.filter(Task.status == status_value)
    rows = query.order_by(Task.priority_score.desc(), Task.created_at.desc()).offset(offset).limit(limit).all()
    return [_serialize_task(task) for task in rows]


@router.post("", status_code=status.HTTP_201_CREATED)
@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_task(payload: TaskCreate, db: Session = Depends(get_db)) -> dict[str, Any]:
    task_type = payload.task_type if isinstance(payload.task_type, TaskType) else TaskType(payload.task_type)
    task = Task(
        project_id=payload.project_id,
        task_type=task_type,
        status=TaskStatus.PENDING,
        url=payload.url,
        title=payload.title,
        description=payload.description,
        impact_score=payload.impact_score,
        effort_score=payload.effort_score,
        meta=dict(payload.metadata or {}),
    )
    task.calculate_priority()
    db.add(task)
    db.commit()
    db.refresh(task)

    await publish_task_created_event(
        db,
        task_id=str(task.id),
        project_id=str(task.project_id),
        task_type=task.task_type.value,
        url=task.url,
        metadata=task.meta or {},
    )
    return _serialize_task(task)


@router.get("/{task_id}")
def get_task(task_id: str, db: Session = Depends(get_db)) -> dict[str, Any]:
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="task_not_found")
    return _serialize_task(task)


@router.patch("/{task_id}")
def update_task(task_id: str, payload: TaskUpdate, db: Session = Depends(get_db)) -> dict[str, Any]:
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="task_not_found")

    updates = payload.model_dump(exclude_unset=True)
    metadata = updates.pop("metadata", None)
    for field_name, value in updates.items():
        if field_name == "status" and value is not None:
            value = TaskStatus(value)
        if field_name == "task_type" and value is not None:
            value = TaskType(value)
        setattr(task, field_name, value)
    if metadata is not None:
        task.meta = metadata
    task.calculate_priority()
    if task.status == TaskStatus.COMPLETED and task.completed_at is None:
        task.completed_at = datetime.now(timezone.utc)
    db.add(task)
    db.commit()
    db.refresh(task)
    return _serialize_task(task)


@router.post("/{task_id}/prioritize")
def reprioritize_task_project(task_id: str, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    task = db.query(Task).filter(Task.id == task_id).first()
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="task_not_found")
    tasks = prioritize_project_tasks(db, str(task.project_id))
    return [_serialize_task(item) for item in tasks]
