
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from config.database_config import get_db
from config.logging_config import get_logger
from database.models import (
    User,
)
from services.api_gateway.auth import (
    get_current_user,
)

from services.api_gateway.score_view import average_full_score
from services.api_gateway.routes.protected.common import (
    _collect_recent_audits,
    _count_project_backlinks,
    _get_owned_projects,
    _latest_ff_score,
    _load_tracked_keywords,
    _project_ids,
    _proxy_management_required_json,
    _serialize_project_private,
    _serialize_user,
)

logger = get_logger(__name__)
router = APIRouter(prefix="/api", tags=["Protected"])


def _active_status(status_value: str) -> bool:
    return status_value in {"queued", "running", "pending", "processing", "in_progress"}


@router.get("/dashboard/stats")
async def get_dashboard_stats(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    projects = _get_owned_projects(current_user, db)
    recent_projects = [_serialize_project_private(project, db) for project in projects[:5]]
    project_ids = _project_ids(projects)

    recent_audit_items = _collect_recent_audits(db, project_ids, limit=50)
    active_audits = sum(1 for item in recent_audit_items if _active_status(str(item.get("status") or "")))
    recent_audits = [
        {
            "id": item["id"],
            "url": item["url"],
            "score": item["score"],
            "status": "completed" if item["status"] not in {"queued", "running", "pending", "processing", "in_progress", "failed"} else item["status"],
        }
        for item in recent_audit_items[:5]
    ]

    pending_tasks = 0
    completed_tasks = 0
    for project_id in project_ids:
        pending_data = await _proxy_management_required_json(
            "GET",
            ["/api/v1/tasks"],
            params={"project_id": project_id, "status": "pending", "limit": 200},
            unavailable_detail="management_tasks_unavailable",
        )
        completed_data = await _proxy_management_required_json(
            "GET",
            ["/api/v1/tasks"],
            params={"project_id": project_id, "status": "completed", "limit": 200},
            unavailable_detail="management_tasks_unavailable",
        )
        pending_tasks += len(pending_data or [])
        completed_tasks += len(completed_data or [])

    backlink_count = _count_project_backlinks(db, project_ids)
    tracked_keywords = _load_tracked_keywords(db, project_ids)
    avg_ff_score, ff_score_projects = average_full_score(_latest_ff_score(db, project_id) for project_id in project_ids)

    return {
        "totalProjects": len(projects),
        "activeAudits": active_audits,
        "totalKeywords": len(tracked_keywords),
        "totalBacklinks": backlink_count,
        "pendingTasks": pending_tasks,
        "completedTasks": completed_tasks,
        "avgFFScore": avg_ff_score,
        "ffScoreProjects": ff_score_projects,
        "recentProjects": recent_projects,
        "recentAudits": recent_audits,
    }


@router.get("/dashboard")
async def get_dashboard_alias(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    stats = await get_dashboard_stats(current_user=current_user, db=db)
    return {
        "user": _serialize_user(current_user),
        "projects_count": stats["totalProjects"],
        "projects": stats["recentProjects"],
        **stats,
    }
