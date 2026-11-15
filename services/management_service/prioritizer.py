"""One priority formula and ordering for API, scheduler and in-memory queues."""
from typing import Any, cast

from sqlalchemy import case, text
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from services.management_service.models import Task, TaskStatus
from services.management_service.approval_policy import UserActor
from services.management_service.priority_policy import (
    URGENCY_RANK, PriorityTask, QueueTask, calculate_priority as calculate_priority, load_priority, priority_key,
)


def priority_order() -> tuple[ColumnElement[Any], ...]:
    # Legacy cached scores and metadata are not priority_v1 observations.
    canonical = Task.meta['priority']['formula_version'].astext == 'priority_v1'
    urgency = case(*[(canonical & (Task.meta['priority']['inputs']['urgency'].astext == name), rank)
                     for name, rank in URGENCY_RANK.items()], else_=4)
    score = case((canonical, Task.priority_score), else_=None)
    return urgency.asc(), score.desc().nullslast(), Task.created_at.asc(), Task.id.asc()


def calculate_task_priority(task: Task) -> float | None:
    return cast(float | None, load_priority(cast(QueueTask, task).meta).snapshot()['effective_score'])


def prioritize_tasks(tasks: list[Task]) -> list[Task]:
    for task in tasks:
        cast(PriorityTask, task).calculate_priority()
    return sorted(tasks, key=lambda task: priority_key(cast(QueueTask, task)))


def prioritize_project_tasks(db: Session, project_id: str, limit: int | None = None, *, actor: UserActor | None = None) -> list[Task]:
    # Lock before recalculation so a concurrent override cannot be overwritten.
    db.execute(text("SET LOCAL lock_timeout='5s'"))
    rows = db.query(Task).filter(Task.project_id == project_id, Task.status == TaskStatus.PENDING).order_by(
        Task.id.asc()).populate_existing().with_for_update().all()
    if actor is not None:
        from services.management_service.approval_service import ApprovalService
        from shared.projects.roles import Permission
        ApprovalService(db).project_scope(project_id, actor, Permission.MANAGE)
    ordered = prioritize_tasks(rows)
    db.commit()
    return ordered[:limit] if limit is not None else ordered


def get_next_task(db: Session, project_id: str) -> Task | None:
    return db.query(Task).filter(
        Task.project_id == project_id, Task.status == TaskStatus.PENDING,
    ).order_by(*priority_order()).first()


def should_auto_approve(task: Task) -> bool:
    # An estimate/urgency/override never constitutes approval of an immutable version.
    return False
