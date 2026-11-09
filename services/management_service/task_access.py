"""Task authorization and immutable-preview boundary, sharing the HITL authority."""

from typing import Any, Mapping, cast
from sqlalchemy.orm import Session

from services.management_service.approval_policy import ApprovalError, UserActor
from services.management_service.approval_service import ApprovalService, TaskRow
from services.management_service.models import HITLApproval, Task, TaskStatus
from shared.projects.roles import Permission
from shared.projects.scope import ScopeError, parse_scope

USER_METADATA = frozenset({"effort", "urgency"})


def check_user_metadata(metadata: dict[str, Any] | None) -> None:
    if metadata and not metadata.keys() <= USER_METADATA:
        raise ApprovalError("task_system_metadata_forbidden", 422)


def check_target(scope: Mapping[str, Any], url: str) -> None:
    try:
        allowed = scope["status"] == "active" and parse_scope(scope["origin"] + scope["path_prefix"]).contains(url)
    except (ScopeError, TypeError):
        allowed = False
    if not allowed:
        raise ApprovalError("task_target_out_of_scope", 422)


def accessible_task(db: Session, task_id: str, actor: UserActor, permission: Permission, *, lock: bool = False) -> TaskRow:
    query = db.query(Task).filter(Task.id == task_id)
    if lock:
        query = query.populate_existing().with_for_update()
    task = cast(TaskRow | None, query.first())
    if task is None:
        raise ApprovalError("task_not_found", 404)
    ApprovalService(db).project_scope(str(task.project_id), actor, permission)
    return task


def require_editable(db: Session, task: TaskRow) -> None:
    if (task.status != TaskStatus.PENDING or db.query(HITLApproval.id).filter(HITLApproval.task_id == task.id).first() is not None):
        raise ApprovalError("task_preview_or_execution_immutable")
