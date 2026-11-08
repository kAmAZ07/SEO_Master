"""Authoritative, transactional HITL decisions. Publication is a separate command."""

from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Literal, Mapping, Protocol, cast
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from services.management_service.approval_policy import (
    ApprovalError, DecisionRecord, DecisionRequest, UserActor, make_record, record_state,
)
from services.management_service.models import HITLApproval, HITLStatus, Task, TaskStatus
from services.management_service.proposal_handoff import (
    ProposalHandoffError, audit_proposal_request, proposal_result, version_diff,
)
from services.semantic_service.proposals.model import RecommendationVersion, compute_content_hash
from services.semantic_service.proposals.validators import canonical_url
from shared.projects.roles import Permission, has_permission
from shared.projects.scope import ScopeError, parse_scope


# Instance attributes of the legacy Column-based models. Keep the ORM definitions
# unchanged; these narrow views prevent treating instance values as Column objects.
class TaskRow(Protocol):
    id: UUID
    project_id: UUID
    url: str
    status: TaskStatus
    updated_at: datetime


class ApprovalRow(Protocol):
    id: UUID
    project_id: UUID
    task_id: UUID
    status: HITLStatus
    diff_data: dict[str, Any]
    meta: dict[str, Any] | None
    approved_by: str | None
    approved_at: datetime | None
    rejected_by: str | None
    rejected_at: datetime | None
    rejection_reason: str | None
    updated_at: datetime


class ApprovalService:
    def __init__(self, db: Session, *, clock: Callable[[], datetime] | None = None,
                 ttl: timedelta = timedelta(hours=24)):
        self.db = db
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.ttl = ttl

    def _scope(self, project_id: str, actor: UserActor, permission: Permission) -> Mapping[str, Any]:
        # Read the canonical authority, never host-match legacy public.projects.
        row = self.db.execute(text(
            "SELECT p.id, p.workspace_id, p.origin, p.path_prefix, p.status, m.role "
            "FROM audit_schema.projects p JOIN audit_schema.workspace_members m "
            "ON m.workspace_id = p.workspace_id "
            "JOIN public.users u ON u.id = m.user_id "
            "WHERE p.id = :project_id AND m.user_id = :actor_id AND u.is_active IS TRUE FOR SHARE OF p, m, u"
        ), {"project_id": project_id, "actor_id": actor.user_id}).mappings().first()
        if row is None:
            raise ApprovalError("hitl_task_not_found", 404)
        if not has_permission(row["role"], permission):
            raise ApprovalError("hitl_permission_denied", 403)
        return dict(row)

    def accessible_projects(self, actor: UserActor) -> list[str]:
        return list(self.db.execute(text(
            "SELECT p.id FROM audit_schema.projects p JOIN audit_schema.workspace_members m "
            "ON m.workspace_id = p.workspace_id JOIN public.users u ON u.id = m.user_id "
            "WHERE m.user_id = :actor_id AND u.is_active IS TRUE "
            "AND m.role IN ('owner','admin','reviewer','deployer','viewer')"
        ), {"actor_id": actor.user_id}).scalars())

    def readable(self, task_id: str, actor: UserActor) -> tuple[ApprovalRow, TaskRow]:
        task = cast(TaskRow | None, self.db.query(Task).filter(Task.id == task_id).first())
        if task is None:
            raise ApprovalError("hitl_task_not_found", 404)
        self._scope(str(task.project_id), actor, Permission.READ)
        approval = cast(ApprovalRow | None, self.db.query(HITLApproval).filter(HITLApproval.task_id == task.id).first())
        if approval is None or str(approval.project_id) != str(task.project_id):
            raise ApprovalError("hitl_task_not_found", 404)
        return approval, task

    def _version(self, approval: ApprovalRow, task: TaskRow, scope: Mapping[str, Any]) -> RecommendationVersion:
        raw = (approval.diff_data or {}).get("recommendation_version")
        try:
            version = RecommendationVersion.model_validate(raw)
        except ValueError:
            raise ApprovalError("version_bound_preview_required") from None
        if (version.project_id != str(task.project_id) or version.task_id != str(task.id)
                or str(approval.project_id) != version.project_id
                or canonical_url(task.url) != version.target.url
                or version.content_hash != compute_content_hash(version)
                or approval.diff_data != version_diff(version)):
            raise ApprovalError("approval_preview_invalid")
        try:
            project_scope = parse_scope((scope["origin"] or "") + (scope["path_prefix"] or "/"))
        except ScopeError:
            raise ApprovalError("approval_scope_invalid") from None
        if scope["status"] != "active" or not project_scope.contains(version.target.url):
            raise ApprovalError("approval_scope_invalid")
        latest = self.db.execute(text(
            "SELECT version_id, content_hash, payload FROM semantic_schema.recommendation_versions "
            "WHERE project_id = :project_id AND target_url = :target_url AND operation_type = :operation "
            "ORDER BY version DESC LIMIT 1"
        ), {"project_id": version.project_id, "target_url": version.target.url,
            "operation": version.operation_type.value}).mappings().first()
        if (latest is None or latest["version_id"] != version.version_id
                or latest["content_hash"] != version.content_hash
                or latest["payload"] != version.model_dump(mode="json")):
            raise ApprovalError("approval_version_invalidated")
        evidence = self.db.execute(text(
            "SELECT r.audit_id, r.project_id, r.mode, r.status, page.value AS page "
            "FROM audit_schema.crawl_results r "
            "CROSS JOIN LATERAL jsonb_array_elements(r.pages) AS page(value) "
            "WHERE r.project_id = :project_id AND r.mode = 'full' AND r.status = 'completed' "
            "AND page.value->>'url' = :target_url "
            "ORDER BY r.crawled_at DESC, r.audit_id DESC LIMIT 1"
        ), {"project_id": version.project_id, "target_url": version.target.url}).mappings().first()
        if evidence is None:
            raise ApprovalError("approval_evidence_invalidated")
        try:
            request = audit_proposal_request(
                {k: evidence[k] for k in ("audit_id", "project_id", "mode", "status")}
                | {"pages": [evidence["page"]]}, project_id=version.project_id,
                task_id=str(task.id), url=task.url, operation=version.operation_type.value,
            )
            proposal_result({"project_id": version.project_id, "target_url": request.target_url,
                             "items": [{"operation_type": version.operation_type.value, "status": "existing",
                                        "version": version.model_dump(mode="json")}]}, request)
        except ProposalHandoffError:
            raise ApprovalError("approval_evidence_invalidated") from None
        return version

    def authorization_status(self, approval: ApprovalRow, task: TaskRow, actor: UserActor) -> str:
        scope = self._scope(str(task.project_id), actor, Permission.READ)
        try:
            version = self._version(approval, task, scope)
        except ApprovalError:
            return "invalidated"
        metadata = approval.meta or {}
        state = record_state(metadata, version=version, now=self.clock())
        if state == "pending" and approval.status != HITLStatus.PENDING:
            return "invalidated"
        if state == "approved":
            if approval.status != HITLStatus.APPROVED or task.status != TaskStatus.APPROVED:
                return "invalidated"
            record = DecisionRecord.model_validate(metadata["decision_record"])
            if record.approval_id != str(approval.id) or record.workspace_id != scope["workspace_id"]:
                return "invalidated"
            role = self.db.execute(text(
                "SELECT m.role FROM audit_schema.workspace_members m JOIN public.users u ON u.id = m.user_id "
                "WHERE m.workspace_id = :workspace_id AND m.user_id = :actor_id AND u.is_active IS TRUE"
            ), {"workspace_id": record.workspace_id, "actor_id": record.actor_id}).scalar()
            if not isinstance(role, str) or not has_permission(role, Permission.APPROVE):
                return "invalidated"
        return state

    def decide(self, task_id: str, actor: UserActor, request: DecisionRequest,
               action: Literal["approve", "reject", "revoke"]) -> dict[str, Any]:
        if request.auto_deploy:
            raise ApprovalError("version_bound_executor_required")
        try:
            # All decisions for a task use this order and fresh locked rows.
            task = cast(TaskRow | None, self.db.query(Task).filter(
                Task.id == task_id,
            ).populate_existing().with_for_update().first())
            if task is None:
                raise ApprovalError("hitl_task_not_found", 404)
            scope = self._scope(str(task.project_id), actor, Permission.APPROVE)
            approval = cast(ApprovalRow | None, self.db.query(HITLApproval).filter(
                HITLApproval.task_id == task.id,
            ).populate_existing().with_for_update().first())
            if approval is None or str(approval.project_id) != str(task.project_id):
                raise ApprovalError("hitl_task_not_found", 404)
            if str(approval.id) != request.approval_id:
                raise ApprovalError("approval_revision_conflict")
            version = self._version(approval, task, scope)
            if (version.version_id != request.recommendation_version_id
                    or version.content_hash != request.content_hash):
                raise ApprovalError("approval_revision_conflict")
            now = self.clock()
            metadata = dict(approval.meta or {})
            state = record_state(metadata, version=version, now=now)
            if action == "revoke":
                if state != "approved":
                    raise ApprovalError("approval_" + state)
                if task.status != TaskStatus.APPROVED:
                    raise ApprovalError("approval_task_state_conflict")
                metadata["revocation"] = {"actor_id": actor.user_id, "actor_role": scope["role"],
                                          "revoked_at": now.isoformat(), "comment": request.comment}
                task.status = TaskStatus.CANCELLED
                state = "revoked"
            else:
                if state != "pending" or approval.status != HITLStatus.PENDING:
                    raise ApprovalError("approval_already_decided")
                if task.status not in {TaskStatus.PENDING, TaskStatus.IN_PROGRESS}:
                    raise ApprovalError("approval_task_state_conflict")
                record = make_record(
                    approval_id=str(approval.id), task_id=str(task.id), workspace_id=scope["workspace_id"],
                    version=version, actor_id=actor.user_id, actor_role=scope["role"], decision=action,
                    now=now, ttl=self.ttl,
                )
                metadata.update(decision_record=record.model_dump(mode="json"), decision_hash=record.digest(),
                                decision_comment=request.comment)
                if action == "approve":
                    approval.status, task.status = HITLStatus.APPROVED, TaskStatus.APPROVED
                    approval.approved_by, approval.approved_at = actor.user_id, now
                    state = "approved"
                else:
                    approval.status, task.status = HITLStatus.REJECTED, TaskStatus.REJECTED
                    approval.rejected_by, approval.rejected_at = actor.user_id, now
                    approval.rejection_reason = request.comment
                    state = "rejected"
            approval.meta = metadata
            approval.updated_at = task.updated_at = now
            self.db.commit()
            return {"task_id": str(task.id), "project_id": version.project_id,
                    "approval_id": str(approval.id), "status": state,
                    "decision_record": metadata["decision_record"], "approval_hash": metadata["decision_hash"],
                    "approved_by": approval.approved_by,
                    "approved_at": approval.approved_at.isoformat() if approval.approved_at else None,
                    "auto_deployed": False, "publication_status": "not_requested"}
        except Exception:
            self.db.rollback()
            raise
