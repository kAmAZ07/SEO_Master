import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from services.management_service.config import settings
from services.management_service.db.session import SessionLocal
from services.management_service.db.models import Project
from services.management_service.orchestrator import run_optimization_cycle
from services.management_service.prioritizer import prioritize_project_tasks
from config.logging_config import get_logger

from services.management_service.tasks.celery_app import celery_app

logger = get_logger(__name__)


def _run_async(coro):
    return asyncio.run(coro)


@celery_app.task(
    name="services.management_service.tasks.orchestration_tasks.run_optimization_cycle_task"
)
def run_optimization_cycle_task(
    project_id: str,
    url: str,
    task_id: Optional[str] = None,
    correlation_id: Optional[str] = None,
) -> Dict[str, Any]:
    started_at = datetime.now(timezone.utc).isoformat()
    logger.info(
        "Starting optimization cycle task",
        extra={
            "project_id": project_id,
            "url": url,
            "task_id": task_id,
            "correlation_id": correlation_id,
        },
    )

    try:
        success = _run_async(
            run_optimization_cycle(project_id=project_id, url=url, task_id=task_id)
        )
        return {
            "status": "completed" if success else "failed",
            "success": bool(success),
            "project_id": project_id,
            "task_id": task_id,
            "started_at": started_at,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
    except Exception as exc:
        logger.error(
            "Optimization cycle task failed",
            extra={
                "project_id": project_id,
                "task_id": task_id,
                "correlation_id": correlation_id,
                "error": str(exc),
            },
        )
        return {
            "status": "error",
            "success": False,
            "project_id": project_id,
            "task_id": task_id,
            "started_at": started_at,
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "error": str(exc),
        }


@celery_app.task(
    name="services.management_service.tasks.orchestration_tasks.reprioritize_project_tasks"
)
def reprioritize_project_tasks(
    project_id: str,
    limit: Optional[int] = None,
    correlation_id: Optional[str] = None,
) -> Dict[str, Any]:
    db = SessionLocal()
    try:
        tasks = prioritize_project_tasks(db, project_id, limit)
        return {
            "status": "completed",
            "project_id": project_id,
            "tasks_prioritized": len(tasks),
            "correlation_id": correlation_id,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
    finally:
        db.close()


@celery_app.task(
    name="services.management_service.tasks.orchestration_tasks.reprioritize_all_projects"
)
def reprioritize_all_projects(
    correlation_id: Optional[str] = None,
) -> Dict[str, Any]:
    db = SessionLocal()
    try:
        projects = db.query(Project).filter(Project.is_active.is_(True)).all()
        results = []
        for project in projects:
            try:
                tasks = prioritize_project_tasks(db, str(project.id))
                results.append(
                    {
                        "project_id": str(project.id),
                        "tasks_prioritized": len(tasks),
                    }
                )
            except Exception as exc:
                logger.error(
                    "Failed to reprioritize project",
                    extra={"project_id": str(project.id), "error": str(exc)},
                )
                results.append(
                    {
                        "project_id": str(project.id),
                        "error": str(exc),
                    }
                )

        return {
            "status": "completed",
            "projects": results,
            "correlation_id": correlation_id,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
    finally:
        db.close()
