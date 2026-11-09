"""Authoritative, transactional HITL decisions. Publication is a separate command."""

from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Literal, Mapping, Protocol, cast
from uuid import UUID
import uuid

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from services.management_service.approval_policy import (
    ApprovalError, DecisionRecord, DecisionRequest, UserActor, make_record, record_state,
)
from services.management_service.approval_ledger import append_history, snapshot_hash
from services.management_service.models import HITLApproval, HITLHistory, HITLStatus, Task, TaskStatus
from services.management_service.proposal_handoff import (
    ProposalHandoffError, audit_proposal_request, proposal_result, version_diff,
)
from services.semantic_service.proposals.model import RecommendationVersion, compute_content_hash
from services.semantic_service.proposals.validators import canonical_url
from shared.projects.roles import Permission, has_permission
from shared.projects.scope import ScopeError, parse_scope
from shared.schema_state import require_producer_barrier


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
    impact_score: float | None
    recommendation: str | None


class ApprovalService:
    def __init__(self, db: Session, *, clock: Callable[[], datetime] | None = None,
                 ttl: timedelta = timedelta(hours=24)):
        self.db = db
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.ttl = ttl

    def _lock_authority(self) -> None:
        if self.db.scalar(text("SELECT current_setting('transaction_isolation')")) != "read committed":
            raise ApprovalError("authority_isolation_unsupported")
        require_producer_barrier(self.db.connection())
        self.db.execute(text("SET LOCAL lock_timeout='5s'"))
        self.db.execute(text("SELECT pg_advisory_xact_lock(74219031109004)"))

    def prepare(self, version: RecommendationVersion, *, metadata: dict[str, Any],
                impact: float | None = None) -> ApprovalRow:
        """Prepare a preview; regeneration archives old facts and never approves."""
        try:
            task = cast(TaskRow | None, self.db.query(Task).filter(
                Task.id == version.task_id,
            ).populate_existing().with_for_update().first())
            if task is None or str(task.project_id) != version.project_id:
                raise ApprovalError("hitl_task_not_found", 404)
            self._lock_authority()
            scope = self.db.execute(text(
                "SELECT id, workspace_id, origin, path_prefix, status FROM audit_schema.projects "
                "WHERE id = :project_id"
            ), {"project_id": version.project_id}).mappings().first()
            if scope is None:
                raise ApprovalError("hitl_task_not_found", 404)
            candidate = HITLApproval(id=uuid.uuid4(), task_id=task.id, project_id=task.project_id,
                                     status=HITLStatus.PENDING, diff_data=version_diff(version))
            self._version(cast(ApprovalRow, candidate), task, dict(scope))
            approval = cast(ApprovalRow | None, self.db.query(HITLApproval).filter(
                HITLApproval.task_id == task.id,
            ).populate_existing().with_for_update().first())
            if approval is not None and str(approval.project_id) != str(task.project_id):
                raise ApprovalError("hitl_task_not_found", 404)
            if approval is not None and approval.diff_data == version_diff(version):
                append_history(self.db, approval_id=str(approval.id), task_id=str(task.id), version=version,
                               entry_type="prepared", metadata=approval.meta or {}, now=self.clock(),
                               emit=approval.status == HITLStatus.PENDING)
                self.db.commit()
                return approval
            if task.status in {TaskStatus.DEPLOYED, TaskStatus.COMPLETED} or (
                    approval is not None and task.status == TaskStatus.IN_PROGRESS):
                raise ApprovalError("approval_task_state_conflict")
            now = self.clock()
            if approval is not None:
                try:
                    old_version = RecommendationVersion.model_validate(approval.diff_data.get("recommendation_version"))
                except ValueError:
                    raise ApprovalError("legacy_approval_requires_migration") from None
                if (old_version.content_hash != compute_content_hash(old_version)
                        or approval.diff_data != version_diff(old_version)
                        or old_version.task_id != str(task.id)
                        or old_version.project_id != str(task.project_id)):
                    raise ApprovalError("approval_history_invalid")
                old_metadata = dict(approval.meta or {})
                append_history(self.db, approval_id=str(approval.id), task_id=str(task.id), version=old_version,
                               entry_type="prepared", metadata={}, now=now, emit=False)
                if "decision_record" in old_metadata:
                    try:
                        record = DecisionRecord.model_validate(old_metadata["decision_record"])
                    except ValueError:
                        raise ApprovalError("approval_history_invalid") from None
                    if (record.digest() != old_metadata.get("decision_hash")
                            or record.approval_id != str(approval.id)
                            or record.task_id != str(task.id) or record.project_id != version.project_id
                            or record.recommendation_version_id != old_version.version_id
                            or record.recommendation_content_hash != old_version.content_hash
                            or record.target_url != old_version.target.url):
                        raise ApprovalError("approval_history_invalid")
                    append_history(self.db, approval_id=str(approval.id), task_id=str(task.id), version=old_version,
                                   entry_type=record.decision, metadata=old_metadata, now=now, emit=False)
                    if old_metadata.get("revocation") is not None:
                        append_history(self.db, approval_id=str(approval.id), task_id=str(task.id), version=old_version,
                                       entry_type="revoke", metadata=old_metadata, now=now, emit=False)
            else:
                self.db.add(candidate)
                approval = cast(ApprovalRow, candidate)
            prepared = {key: metadata[key] for key in ("saga_id", "correlation_id", "ffscore", "eeat_score") if key in metadata}
            prepared.update(url=version.target.url, recommendation_version_id=version.version_id,
                            recommendation_content_hash=version.content_hash, proposal_version=version.version,
                            execution_mode=version.execution_mode.value, prepared_at=now.isoformat())
            approval.status, task.status = HITLStatus.PENDING, TaskStatus.PENDING
            approval.diff_data, approval.meta = version_diff(version), prepared
            approval.approved_by = approval.approved_at = None
            approval.rejected_by = approval.rejected_at = approval.rejection_reason = None
            approval.impact_score = impact
            approval.recommendation = "Review generated SEO changes before deployment"
            approval.updated_at = task.updated_at = now
            self.db.flush()
            append_history(self.db, approval_id=str(approval.id), task_id=str(task.id), version=version,
                           entry_type="prepared", metadata=prepared, now=now)
            self.db.commit()
            return approval
        except Exception:
            self.db.rollback()
            raise

    def _scope(self, project_id: str, actor: UserActor, permission: Permission) -> Mapping[str, Any]:
        # Read the canonical authority, never host-match legacy public.projects.
        self._lock_authority()
        row = self.db.execute(text(
            "SELECT p.id, p.workspace_id, p.origin, p.path_prefix, p.status, m.role "
            "FROM audit_schema.projects p JOIN audit_schema.workspace_members m "
            "ON m.workspace_id = p.workspace_id "
            "JOIN public.users u ON u.id = m.user_id "
            "WHERE p.id = :project_id AND m.user_id = :actor_id AND u.is_active IS TRUE"
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

    def project_scope(self, project_id: str, actor: UserActor, permission: Permission) -> Mapping[str, Any]:
        return self._scope(project_id, actor, permission)

    def readable(self, task_id: str, actor: UserActor) -> tuple[ApprovalRow, TaskRow]:
        task = cast(TaskRow | None, self.db.query(Task).filter(Task.id == task_id).first())
        if task is None:
            raise ApprovalError("hitl_task_not_found", 404)
        self._scope(str(task.project_id), actor, Permission.READ)
        approval = cast(ApprovalRow | None, self.db.query(HITLApproval).filter(HITLApproval.task_id == task.id).first())
        if approval is None or str(approval.project_id) != str(task.project_id):
            raise ApprovalError("hitl_task_not_found", 404)
        return approval, task

    def history(self, task_id: str, actor: UserActor, *, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        if not 1 <= limit <= 100 or not 0 <= offset <= 100_000:
            raise ApprovalError("history_limit_invalid", 422)
        _, task = self.readable(task_id, actor)
        rows = self.db.execute(select(
            HITLHistory.id, HITLHistory.entry_type, HITLHistory.version_id, HITLHistory.content_hash,
            HITLHistory.entry_hash, HITLHistory.meta, HITLHistory.created_at,
        ).where(HITLHistory.task_id == task.id, HITLHistory.project_id == task.project_id).order_by(
            HITLHistory.created_at, HITLHistory.id,
        ).offset(offset).limit(limit)).all()
        result = []
        for row in rows:
            if snapshot_hash(row[5]) != row[4]:
                raise ApprovalError("approval_history_corrupted")
            result.append({"id": str(row[0]), "entry_type": row[1], "version_id": row[2],
                           "content_hash": row[3], "entry_hash": row[4], "snapshot": row[5],
                           "created_at": row[6].isoformat()})
        return result

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
            # Preserve the reviewed entity binding; fresh page facts/evidence still come only from Audit.
            request = type(request).model_validate({**request.model_dump(mode="json"), "entity": version.target.entity})
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
            append_history(self.db, approval_id=str(approval.id), task_id=str(task.id), version=version,
                           entry_type=action, metadata=metadata, now=now)
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
