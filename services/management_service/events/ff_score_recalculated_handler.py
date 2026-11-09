from datetime import datetime, timezone
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from services.management_service.db.models import Task, Project
from services.management_service.score_contract import parse_score_event
from config.logging_config import get_logger

logger = get_logger(__name__)


def handle_ff_score_recalculated_event(
    db: Session,
    event: Dict[str, Any],
    correlation_id: Optional[str] = None,
) -> Dict[str, Any]:
    # Проверка до query/add/commit: невалидное событие не меняет состояние.
    observation = parse_score_event(event)
    project_id = observation.project_id
    ff_score = observation.ff_score
    eeat_score = observation.eeat_score
    stamp = datetime.now(timezone.utc).isoformat()
    score_meta = {
        "ffscore": ff_score,
        "ffscore_status": observation.status,
        "ffscore_formula_version": observation.formula_version,
        "ffscore_updated_at": stamp,
    }
    if observation.eeat_present:
        score_meta.update(eeat_score=eeat_score, eeat_status=observation.eeat_status)

    updated_tasks = 0
    updated_project = False

    project = db.query(Project).filter(Project.id == project_id).first()
    if project:
        project.meta = {
            **(project.meta or {}),
            **score_meta,
        }
        db.add(project)
        updated_project = True

    tasks = db.query(Task).filter(Task.project_id == project_id).all()
    for task in tasks:
        # Новое insufficient_data должно удалить устаревший полный балл.
        # Новый dict также нужен для отслеживания JSONB SQLAlchemy.
        meta = {
            **(task.meta or {}),
            "current_ffscore": ff_score,
            "ffscore_status": observation.status,
            "ffscore_formula_version": observation.formula_version,
            "ffscore_updated_at": stamp,
        }
        if observation.eeat_present:
            meta.update(current_eeat=eeat_score, eeat_status=observation.eeat_status)
        task.meta = meta
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
        "status": observation.status,
        "formula_version": observation.formula_version,
        "eeat_score": eeat_score,
        "updated_tasks": updated_tasks,
        "updated_project": updated_project,
    }

