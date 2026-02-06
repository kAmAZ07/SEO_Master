from typing import List, Dict, Any, Optional
from enum import Enum
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logging import logger
from app.db.models import Task, TaskType, TaskStatus


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


TASK_TYPE_EFFORT_MAP = {
    TaskType.UPDATE_META_TAGS: EffortLevel.MINIMAL,
    TaskType.UPDATE_SCHEMA_ORG: EffortLevel.LOW,
    TaskType.UPDATE_H1: EffortLevel.MINIMAL,
    TaskType.OPTIMIZE_IMAGES: EffortLevel.MEDIUM,
    TaskType.FIX_BROKEN_LINKS: EffortLevel.LOW,
    TaskType.IMPROVE_PAGE_SPEED: EffortLevel.HIGH,
    TaskType.REWRITE_CONTENT: EffortLevel.VERY_HIGH,
    TaskType.ADD_INTERNAL_LINKS: EffortLevel.LOW,
    TaskType.UPDATE_CANONICAL: EffortLevel.MINIMAL,
    TaskType.FIX_DUPLICATE_CONTENT: EffortLevel.MEDIUM,
}


def calculate_combined_score(
    current_ffscore: Optional[float],
    current_eeat: Optional[float],
    ffscore_weight: float = 0.7,
    eeat_weight: float = 0.3
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
    weighted_sum = sum(s * w for s, w in zip(scores, weights))
    
    return weighted_sum / total_weight


def calculate_impact(
    current_ffscore: Optional[float],
    expected_ffscore: Optional[float],
    current_eeat: Optional[float] = None,
    expected_eeat: Optional[float] = None
) -> float:
    current_combined = calculate_combined_score(current_ffscore, current_eeat)
    expected_combined = calculate_combined_score(expected_ffscore, expected_eeat)
    
    improvement = expected_combined - current_combined
    
    normalized_impact = min(max(improvement / 100.0, 0.0), 1.0)
    
    return normalized_impact


def calculate_urgency(
    current_ffscore: Optional[float],
    current_eeat: Optional[float] = None
) -> float:
    combined_score = calculate_combined_score(current_ffscore, current_eeat)
    
    if combined_score < 30:
        return 1.0
    elif combined_score < 50:
        return 0.8
    elif combined_score < 70:
        return 0.6
    elif combined_score < 85:
        return 0.4
    else:
        return 0.2


def get_urgency_level(
    current_ffscore: Optional[float],
    current_eeat: Optional[float] = None
) -> UrgencyLevel:
    combined_score = calculate_combined_score(current_ffscore, current_eeat)
    
    if combined_score < 30:
        return UrgencyLevel.CRITICAL
    elif combined_score < 50:
        return UrgencyLevel.HIGH
    elif combined_score < 70:
        return UrgencyLevel.MEDIUM
    else:
        return UrgencyLevel.LOW


def calculate_effort(task_type: TaskType, metadata: Dict[str, Any] = None) -> float:
    effort_level = TASK_TYPE_EFFORT_MAP.get(task_type, EffortLevel.MEDIUM)
    
    if metadata and "custom_effort" in metadata:
        effort_level = EffortLevel(metadata["custom_effort"])
    
    normalized_effort = effort_level.value / 5.0
    
    return normalized_effort


def calculate_priority(
    current_ffscore: Optional[float],
    expected_ffscore: Optional[float],
    task_type: TaskType,
    current_eeat: Optional[float] = None,
    expected_eeat: Optional[float] = None,
    metadata: Dict[str, Any] = None
) -> float:
    impact = calculate_impact(current_ffscore, expected_ffscore, current_eeat, expected_eeat)
    urgency = calculate_urgency(current_ffscore, current_eeat)
    effort = calculate_effort(task_type, metadata)
    
    effort_inverse = 1.0 / effort if effort > 0 else 1.0
    effort_normalized = min(effort_inverse / 5.0, 1.0)
    
    priority = (
        impact * settings.TASK_PRIORITY_IMPACT_WEIGHT +
        urgency * settings.TASK_PRIORITY_URGENCY_WEIGHT +
        effort_normalized * settings.TASK_PRIORITY_EFFORT_WEIGHT
    )
    
    return round(priority, 4)


def calculate_task_priority(task: Task) -> float:
    metadata = task.metadata or {}
    
    current_ffscore = metadata.get("current_ffscore")
    expected_ffscore = metadata.get("expected_ffscore")
    current_eeat = metadata.get("current_eeat")
    expected_eeat = metadata.get("expected_eeat")
    
    priority = calculate_priority(
        current_ffscore=current_ffscore,
        expected_ffscore=expected_ffscore,
        current_eeat=current_eeat,
        expected_eeat=expected_eeat,
        task_type=task.task_type,
        metadata=metadata
    )
    
    return priority


def prioritize_tasks(tasks: List[Task]) -> List[Task]:
    tasks_with_priority = []
    
    for task in tasks:
        priority_score = calculate_task_priority(task)
        
        task.priority_score = priority_score
        task.metadata = task.metadata or {}
        task.metadata["priority_score"] = priority_score
        task.metadata["impact"] = calculate_impact(
            task.metadata.get("current_ffscore"),
            task.metadata.get("expected_ffscore"),
            task.metadata.get("current_eeat"),
            task.metadata.get("expected_eeat")
        )
        task.metadata["urgency"] = calculate_urgency(
            task.metadata.get("current_ffscore"),
            task.metadata.get("current_eeat")
        )
        task.metadata["urgency_level"] = get_urgency_level(
            task.metadata.get("current_ffscore"),
            task.metadata.get("current_eeat")
        )
        task.metadata["effort"] = calculate_effort(task.task_type, task.metadata)
        
        tasks_with_priority.append(task)
    
    sorted_tasks = sorted(
        tasks_with_priority,
        key=lambda t: (t.priority_score, t.created_at),
        reverse=True
    )
    
    return sorted_tasks


def prioritize_project_tasks(db: Session, project_id: str, limit: int = None) -> List[Task]:
    query = db.query(Task).filter(
        Task.project_id == project_id,
        Task.status.in_([TaskStatus.PENDING, TaskStatus.QUEUED])
    )
    
    tasks = query.all()
    
    if not tasks:
        logger.info(f"No tasks to prioritize for project {project_id}")
        return []
    
    prioritized_tasks = prioritize_tasks(tasks)
    
    for task in prioritized_tasks:
        db.add(task)
    
    db.commit()
    
    logger.info(
        f"Prioritized {len(prioritized_tasks)} tasks for project {project_id}",
        extra={
            "project_id": project_id,
            "tasks_count": len(prioritized_tasks),
            "top_priority": prioritized_tasks[0].priority_score if prioritized_tasks else None
        }
    )
    
    if limit:
        return prioritized_tasks[:limit]
    
    return prioritized_tasks


def get_next_task(db: Session, project_id: str) -> Optional[Task]:
    task = db.query(Task).filter(
        Task.project_id == project_id,
        Task.status == TaskStatus.PENDING
    ).order_by(
        Task.priority_score.desc(),
        Task.created_at.asc()
    ).first()
    
    return task


def should_auto_approve(task: Task) -> bool:
    if not settings.HITL_AUTO_APPROVE_LOW_RISK:
        return False
    
    metadata = task.metadata or {}
    
    low_risk_types = [
        TaskType.UPDATE_META_TAGS,
        TaskType.UPDATE_CANONICAL,
        TaskType.ADD_INTERNAL_LINKS
    ]
    
    if task.task_type not in low_risk_types:
        return False
    
    impact = metadata.get("impact", 0)
    if impact > 0.3:
        return False
    
    effort = metadata.get("effort", 1)
    if effort > 0.4:
        return False
    
    return True
