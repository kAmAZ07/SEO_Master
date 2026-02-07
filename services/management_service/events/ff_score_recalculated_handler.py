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


def handle_ff_score_recalculated_event(
    db: Session,
    event: Dict[str, Any],
    correlation_id: Optional[str] = None,
) -> Dict[str, Any]:
    payload = _extract_payload(event)

    project_id = payload.get("project_id")
    ff_score = payload.get("ff_score")
    if ff_score is None:
        ff_score = payload.get("ffscore")
    eeat_score = payload.get("eeat_score")
    if eeat_score is None:
        eeat_score = payload.get("eeat")

    if not project_id:
        raise ValueError("project_id is required in event payload")

    updated_tasks = 0
    updated_project = False

    project = db.query(Project).filter(Project.id == project_id).first()
    if project:
        project.metadata = {
            **(project.metadata or {}),
            "ffscore": ff_score,
            "eeat_score": eeat_score,
            "ffscore_updated_at": datetime.now(timezone.utc).isoformat(),
        }
        db.add(project)
        updated_project = True

    tasks = db.query(Task).filter(Task.project_id == project_id).all()
    for task in tasks:
        meta = task.metadata or {}
        if ff_score is not None:
            meta["current_ffscore"] = ff_score
        if eeat_score is not None:
            meta["current_eeat"] = eeat_score
        task.metadata = meta
        if hasattr(task, "calculate_priority"):
            task.calculate_priority()
        db.add(task)
        updated_tasks += 1

    if updated_tasks or updated_project:
        db.commit()

    logger.info(
        "FFScoreRecalculated event handled",
        extra={
            "project_id": project_id,
            "ff_score": ff_score,
            "eeat_score": eeat_score,
            "correlation_id": correlation_id,
            "updated_tasks": updated_tasks,
            "updated_project": updated_project,
        },
    )

    return {
        "project_id": project_id,
        "ff_score": ff_score,
        "eeat_score": eeat_score,
        "updated_tasks": updated_tasks,
        "updated_project": updated_project,
    }
