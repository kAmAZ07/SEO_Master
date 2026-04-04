from enum import Enum
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from services.management_service.config import settings
from services.management_service.db.models import Task, TaskStatus, TaskType
from config.logging_config import get_logger


logger = get_logger(__name__)


class UrgencyLevel(str, Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class EffortLevel(int, Enum):
    MINIMAL = 1
    LOW = 2
    MEDIUM = 3
    HIGH = 4
    VERY_HIGH = 5


TASK_TYPE_EFFORT_MAP: Dict[TaskType, EffortLevel] = {
    TaskType.UPDATE_META: EffortLevel.MINIMAL,
    TaskType.UPDATE_SCHEMA: EffortLevel.LOW,
    TaskType.UPDATE_CONTENT: EffortLevel.VERY_HIGH,
    TaskType.ADD_INTERNAL_LINKS: EffortLevel.LOW,
    TaskType.FIX_404: EffortLevel.MEDIUM,
    TaskType.UPDATE_TILDA_PAGE: EffortLevel.MEDIUM,
    TaskType.OPTIMIZE_IMAGES: EffortLevel.MEDIUM,
    TaskType.FIX_BROKEN_LINKS: EffortLevel.LOW,
}


def calculate_combined_score(
    current_ffscore: Optional[float],
    current_eeat: Optional[float],
    ffscore_weight: float = 0.7,
    eeat_weight: float = 0.3,
) -> float:
    scores = []
    weights = []

    if current_ffscore is not None:
        scores.append(current_ffscore)
        weights.append(ffscore_weight)

    if current_eeat is not None:
        scores.append(current_eeat)
        weights.append(eeat_weight)

    if not scores:
        return 50.0

    total_weight = sum(weights)
    weighted_sum = sum(score * weight for score, weight in zip(scores, weights))
    return weighted_sum / total_weight


def calculate_impact(
    current_ffscore: Optional[float],
    expected_ffscore: Optional[float],
    current_eeat: Optional[float] = None,
    expected_eeat: Optional[float] = None,
) -> float:
    current_combined = calculate_combined_score(current_ffscore, current_eeat)
    expected_combined = calculate_combined_score(expected_ffscore, expected_eeat)

    improvement = expected_combined - current_combined
    return min(max(improvement / 100.0, 0.0), 1.0)


def calculate_urgency(
    current_ffscore: Optional[float],
    current_eeat: Optional[float] = None,
) -> float:
    combined_score = calculate_combined_score(current_ffscore, current_eeat)

    if combined_score < 30:
        return 1.0
    if combined_score < 50:
        return 0.8
    if combined_score < 70:
        return 0.6
    if combined_score < 85:
        return 0.4
    return 0.2


def get_urgency_level(
    current_ffscore: Optional[float],
    current_eeat: Optional[float] = None,
) -> UrgencyLevel:
    combined_score = calculate_combined_score(current_ffscore, current_eeat)

    if combined_score < 30:
        return UrgencyLevel.CRITICAL
    if combined_score < 50:
        return UrgencyLevel.HIGH
    if combined_score < 70:
        return UrgencyLevel.MEDIUM
    return UrgencyLevel.LOW


def calculate_effort(task_type: TaskType, metadata: Optional[Dict[str, Any]] = None) -> float:
    effort_level = TASK_TYPE_EFFORT_MAP.get(task_type, EffortLevel.MEDIUM)

    if metadata and "custom_effort" in metadata:
        try:
            effort_level = EffortLevel(int(metadata["custom_effort"]))
        except Exception:
            logger.warning(
                "Invalid custom_effort value, fallback to default",
                extra={"task_type": task_type.value, "custom_effort": metadata.get("custom_effort")},
            )

    return effort_level.value / 5.0


def calculate_priority(
    current_ffscore: Optional[float],
    expected_ffscore: Optional[float],
    task_type: TaskType,
    current_eeat: Optional[float] = None,
    expected_eeat: Optional[float] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> float:
    impact = calculate_impact(current_ffscore, expected_ffscore, current_eeat, expected_eeat)
    urgency = calculate_urgency(current_ffscore, current_eeat)
    effort = calculate_effort(task_type, metadata)

    effort_inverse = 1.0 / effort if effort > 0 else 1.0
    effort_normalized = min(effort_inverse / 5.0, 1.0)

    priority = (
        impact * settings.TASK_PRIORITY_IMPACT_WEIGHT
        + urgency * settings.TASK_PRIORITY_URGENCY_WEIGHT
        + effort_normalized * settings.TASK_PRIORITY_EFFORT_WEIGHT
    )

    return round(priority, 4)


def calculate_task_priority(task: Task) -> float:
    metadata = task.meta or {}

    return calculate_priority(
        current_ffscore=metadata.get("current_ffscore"),
        expected_ffscore=metadata.get("expected_ffscore"),
        current_eeat=metadata.get("current_eeat"),
        expected_eeat=metadata.get("expected_eeat"),
        task_type=task.task_type,
        metadata=metadata,
    )


def prioritize_tasks(tasks: List[Task]) -> List[Task]:
    prioritized: List[Task] = []

    for task in tasks:
        metadata = task.meta or {}

        priority_score = calculate_task_priority(task)
        impact = calculate_impact(
            metadata.get("current_ffscore"),
            metadata.get("expected_ffscore"),
            metadata.get("current_eeat"),
            metadata.get("expected_eeat"),
        )
        urgency = calculate_urgency(
            metadata.get("current_ffscore"),
            metadata.get("current_eeat"),
        )
        urgency_level = get_urgency_level(
            metadata.get("current_ffscore"),
            metadata.get("current_eeat"),
        )
        effort = calculate_effort(task.task_type, metadata)

        task.priority_score = priority_score
        task.meta = {
            **metadata,
            "priority_score": priority_score,
            "impact": impact,
            "urgency": urgency,
            "urgency_level": urgency_level.value,
            "effort": effort,
        }
        prioritized.append(task)

    return sorted(
        prioritized,
        key=lambda item: (item.priority_score, item.created_at),
        reverse=True,
    )


def prioritize_project_tasks(db: Session, project_id: str, limit: Optional[int] = None) -> List[Task]:
    query = db.query(Task).filter(
        Task.project_id == project_id,
        Task.status == TaskStatus.PENDING,
    )

    tasks = query.all()
    if not tasks:
        logger.info("No tasks to prioritize", extra={"project_id": project_id})
        return []

    prioritized_tasks = prioritize_tasks(tasks)
    for task in prioritized_tasks:
        db.add(task)
    db.commit()

    logger.info(
        "Tasks prioritized",
        extra={
            "project_id": project_id,
            "tasks_count": len(prioritized_tasks),
            "top_priority": prioritized_tasks[0].priority_score if prioritized_tasks else None,
        },
    )

    if limit:
        return prioritized_tasks[:limit]
    return prioritized_tasks


def get_next_task(db: Session, project_id: str) -> Optional[Task]:
    return (
        db.query(Task)
        .filter(Task.project_id == project_id, Task.status == TaskStatus.PENDING)
        .order_by(Task.priority_score.desc(), Task.created_at.asc())
        .first()
    )


def should_auto_approve(task: Task) -> bool:
    if not settings.HITL_AUTO_APPROVE_LOW_RISK:
        return False

    low_risk_types = {
        TaskType.UPDATE_META,
        TaskType.ADD_INTERNAL_LINKS,
        TaskType.FIX_BROKEN_LINKS,
    }
    if task.task_type not in low_risk_types:
        return False

    metadata = task.meta or {}
    impact = float(metadata.get("impact", 0.0) or 0.0)
    effort = float(metadata.get("effort", 1.0) or 1.0)

    return impact <= 0.3 and effort <= 0.4



