from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from services.management_service.db.models import Task, Project
from config.logging_config import get_logger

logger = get_logger(__name__)


def _extract_payload(event: Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(event, dict) and "payload" in event:
        return event.get("payload") or {}
    return event or {}


def handle_crawl_completed_event(
    db: Session,
    event: Dict[str, Any],
    correlation_id: Optional[str] = None,
) -> Dict[str, Any]:
    payload = _extract_payload(event)

    task_id = payload.get("task_id")
    project_id = payload.get("project_id")
    crawl_id = payload.get("crawl_id") or payload.get("audit_id")
    audit_id = payload.get("audit_id") or payload.get("crawl_id")
    summary = payload.get("summary") or {}

    updated_tasks = 0
    updated_project = False

    if task_id:
        task = db.query(Task).filter(Task.id == task_id).first()
        if not task:
            raise ValueError(f"Task {task_id} not found")
        task.metadata = {
            **(task.metadata or {}),
            "crawl_id": crawl_id,
            "audit_result_id": audit_id,
            "audit_summary": summary,
            "correlation_id": correlation_id,
            "audit_completed_at": datetime.now(timezone.utc).isoformat(),
        }
        db.add(task)
        updated_tasks += 1
        if not project_id:
            project_id = str(task.project_id)

    if project_id:
        project = db.query(Project).filter(Project.id == project_id).first()
        if project:
            project.metadata = {
                **(project.metadata or {}),
                "latest_crawl_id": crawl_id,
                "latest_audit_id": audit_id,
                "audit_summary": summary,
                "audit_completed_at": datetime.now(timezone.utc).isoformat(),
            }
            db.add(project)
            updated_project = True

    if updated_tasks or updated_project:
        db.commit()

    logger.info(
        "CrawlCompleted event handled",
        extra={
            "task_id": task_id,
            "project_id": project_id,
            "crawl_id": crawl_id,
            "correlation_id": correlation_id,
            "updated_tasks": updated_tasks,
            "updated_project": updated_project,
        },
    )

    return {
        "task_id": task_id,
        "project_id": project_id,
        "crawl_id": crawl_id,
        "audit_id": audit_id,
        "updated_tasks": updated_tasks,
        "updated_project": updated_project,
    }
