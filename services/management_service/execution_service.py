"""The sole durable DeploymentOperation command, stored in reserved changelog rows.

Network I/O belongs to Client Gateway. Task/approval/operation lock order is fixed.
JSON command records are immutable and separately hashed; only delivery state changes.
"""

from datetime import datetime, timedelta, timezone
import hashlib
from typing import Any, Callable, Mapping, Protocol, cast
from uuid import UUID

from sqlalchemy import null, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from services.management_service.approval_policy import ApprovalError, DecisionRecord, UserActor, record_state
from services.management_service.approval_service import ApprovalService, ApprovalRow, TaskRow
from services.management_service.models import Changelog, HITLApproval, HITLStatus, Task, TaskStatus
from shared.hitl_execution import ExecutionRequest, WordPressBinding, canonical, digest, make_command, validate_receipt
from shared.projects.roles import Permission, has_permission
from shared.projects.scope import parse_scope
from shared.projects.authorization_state import is_fresh
from services.semantic_service.proposals.model import RecommendationVersion
from services.management_service.recovery_policy import RecoveryState
from services.management_service.preparation_policy import PreparationState

SOURCE = "hitl_executor_v1"
ENTITY = "deployment_operation"


class OperationRow(Protocol):
    id: UUID
    task_id: UUID
    project_id: UUID
    meta: dict[str, Any]
    before_value: dict[str, Any] | None
    after_value: dict[str, Any] | None
    applied: bool


def record_hash(value: dict[str, Any]) -> str:
    return hashlib.sha256(("SEO-MASTER-DEPLOYMENT-RECORD-V1\n" + canonical(value)).encode()).hexdigest()


class ExecutionService:
    def __init__(self, db: Session, *, clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc), fresh_days: int = 30):
        self.db, self.clock = db, clock
        self.approvals = ApprovalService(db, clock=clock)
        self.fresh_days = fresh_days

    def _authorize(self, references: ExecutionRequest, actor: UserActor) -> tuple[TaskRow, ApprovalRow, RecommendationVersion, Mapping[str, Any], DecisionRecord, datetime]:
        task = cast(TaskRow | None, self.db.query(Task).filter(Task.id == references.task_id).populate_existing().with_for_update().first())
        if task is None:
            raise ApprovalError("execution_not_found", 404)
        scope = self.approvals.project_scope(str(task.project_id), actor, Permission.DEPLOY)
        approval = cast(ApprovalRow | None, self.db.query(HITLApproval).filter(HITLApproval.task_id == task.id).populate_existing().with_for_update().first())
        if approval is None or str(approval.id) != references.approval_id or str(approval.project_id) != str(task.project_id):
            raise ApprovalError("execution_approval_required")
        version = self.approvals._version(approval, task, scope)
        if version.version_id != references.recommendation_version_id or version.content_hash != references.content_hash:
            raise ApprovalError("approval_revision_conflict")
        if (approval.status != HITLStatus.APPROVED or task.status != TaskStatus.APPROVED
                or record_state(approval.meta or {}, version=version, now=self.clock()) != "approved"):
            raise ApprovalError("execution_approval_invalid")
        decision = DecisionRecord.model_validate((approval.meta or {})["decision_record"])
        if decision.workspace_id != scope["workspace_id"] or decision.approval_id != str(approval.id):
            raise ApprovalError("execution_approval_invalid")
        approver_role = self.db.execute(text(
            "SELECT m.role FROM audit_schema.workspace_members m JOIN public.users u ON u.id=m.user_id "
            "WHERE m.workspace_id=:workspace AND m.user_id=:actor AND u.is_active IS TRUE"
        ), {"workspace": decision.workspace_id, "actor": decision.actor_id}).scalar()
        if not isinstance(approver_role, str) or not has_permission(approver_role, Permission.APPROVE):
            raise ApprovalError("execution_approver_permission_lost")
        verified = self.db.execute(text(
            "SELECT verified_at FROM audit_schema.domain_authorizations WHERE project_id=:project AND workspace_id=:workspace "
            "AND status='verified' AND scope_origin=:origin AND scope_path_prefix=:prefix "
            "AND revoked_at IS NULL"
        ), {"project": version.project_id, "workspace": decision.workspace_id, "origin": scope["origin"],
            "prefix": scope["path_prefix"]}).scalar()
        if verified is None or not is_fresh("verified", verified, self.fresh_days, self.clock()):
            raise ApprovalError("execution_domain_authorization_required")
        valid_until = min(decision.expires_at, verified + timedelta(days=self.fresh_days))
        if self.clock() >= valid_until:
            raise ApprovalError("execution_expired")
        return task, approval, version, scope, decision, valid_until

    def context(self, references: ExecutionRequest, actor: UserActor) -> dict[str, Any]:
        try:
            _, _, version, _, _, _ = self._authorize(references, actor)
            return version.model_dump(mode="json")
        finally:
            self.db.rollback()  # Read transaction and SHARE/UPDATE locks never cross HTTP.

    @staticmethod
    def _intent(references: ExecutionRequest, actor: UserActor, version: RecommendationVersion,
                scope: Mapping[str, Any], decision: DecisionRecord) -> dict[str, Any]:
        return {**references.model_dump(), "project_id": version.project_id, "workspace_id": scope["workspace_id"],
                "actor_id": actor.user_id, "actor_role": scope["role"], "decision_hash": decision.digest(),
                "version": version.model_dump(mode="json")}

    def queue(self, references: ExecutionRequest, actor: UserActor) -> dict[str, Any]:
        try:
            task, _, version, scope, decision, _ = self._authorize(references, actor)
            existing = self.db.query(Changelog).filter(Changelog.id == references.operation_id).first()
            if existing is not None:
                row = self._row(references.operation_id, lock=True)
                if (any(row.meta["record"].get(key) != value for key, value in references.model_dump().items())
                        or row.meta["record"]["actor_id"] != actor.user_id):
                    raise ApprovalError("execution_idempotency_conflict")
            else:
                # Preserve the old explicit expiry/new-ID policy for never-sent ready
                # commands. Begun/unknown/manual operations are never released here.
                for previous in cast(list[OperationRow], self.db.query(Changelog).filter(
                        Changelog.task_id == task.id, Changelog.source == SOURCE).with_for_update().all()):
                    if (previous.meta["state"] == "ready" and
                            previous.meta["record"]["command"]["authorization_deadline"] <= int(self.clock().timestamp())):
                        metadata = dict(previous.meta)
                        if "preparation" in metadata:
                            metadata["preparation"] = PreparationState.load(metadata["preparation"]).transition("cancelled", self.clock()).dump()
                        previous.meta = {**metadata, "state": "cancelled", "cancel_actor_id": actor.user_id,
                                         "cancelled_at": self.clock().isoformat(), "cancel_reason": "authorization_deadline_expired"}
                self.db.flush()
                intent = self._intent(references, actor, version, scope, decision)
                row = cast(OperationRow, Changelog(id=UUID(references.operation_id), task_id=task.id, project_id=task.project_id,
                    entity_id=references.operation_id, entity_type=ENTITY, change_type="execute_v1", source=SOURCE,
                    applied=False, before_value=null(), after_value=null(),
                    meta={"intent": intent, "intent_hash": record_hash(intent), "record": intent,
                          "record_hash": record_hash(intent), "prepared": False, "state": "queued",
                          "preparation": PreparationState.initial(self.clock()).dump()}))
                self.db.add(row)
                self.db.flush()
            self.db.commit()
            return self.serialize(row)
        except IntegrityError:
            self.db.rollback()
            raise ApprovalError("execution_already_exists") from None
        except Exception:
            self.db.rollback()
            raise

    def _row(self, operation_id: str, *, lock: bool = False) -> OperationRow:
        query = self.db.query(Changelog).filter(Changelog.id == operation_id, Changelog.source == SOURCE, Changelog.entity_type == ENTITY)
        if lock:
            query = query.populate_existing().with_for_update()
        row = cast(OperationRow | None, query.first())
        if row is None:
            raise ApprovalError("execution_not_found", 404)
        metadata = row.meta or {}
        record = metadata.get("record")
        if (not isinstance(record, dict) or metadata.get("record_hash") != record_hash(record)
                or str(row.id) != record.get("operation_id") or str(row.task_id) != record.get("task_id")
                or str(row.project_id) != record.get("project_id")
                ):
            raise ApprovalError("execution_record_corrupted")
        if metadata.get("prepared") is False:
            if (record != metadata.get("intent") or metadata.get("intent_hash") != record_hash(record)
                    or row.before_value is not None or row.after_value is not None):
                raise ApprovalError("execution_record_corrupted")
        else:
            if (digest(record.get("command")) != record.get("request_hash")
                    or row.before_value != record.get("before") or row.after_value != record.get("after")):
                raise ApprovalError("execution_record_corrupted")
            if "intent" in metadata:
                prefix = {key: value for key, value in record.items() if key not in {"command", "request_hash", "before", "after", "binding"}}
                if prefix != metadata["intent"] or metadata["intent_hash"] != record_hash(prefix):
                    raise ApprovalError("execution_record_corrupted")
        return row

    @staticmethod
    def serialize(row: OperationRow) -> dict[str, Any]:
        return {"operation_id": str(row.id), "state": row.meta["state"], "record": row.meta["record"],
                "record_hash": row.meta["record_hash"], "receipt": row.meta.get("receipt"),
                "cancellation": {"actor_id": row.meta["cancel_actor_id"], "at": row.meta["cancelled_at"], "reason": row.meta["cancel_reason"]} if "cancel_actor_id" in row.meta else None,
                "prepared": row.meta.get("prepared", True),
                "preparation": PreparationState.load(row.meta["preparation"]).summary() if "preparation" in row.meta else None,
                "recovery": RecoveryState.load(row.meta["recovery"]).summary() if "recovery" in row.meta else None}

    def read(self, operation_id: str, actor: UserActor) -> dict[str, Any]:
        row = self._row(operation_id)
        self.approvals.project_scope(str(row.project_id), actor, Permission.READ)
        return self.serialize(row)

    def reserve(self, references: ExecutionRequest, actor: UserActor, binding: WordPressBinding, before: Any,
                *, preparation_lease: str | None = None) -> dict[str, Any]:
        try:
            task, _, version, scope, decision, valid_until = self._authorize(references, actor)
            if parse_scope(binding.base_url).origin != version.target.origin:
                raise ApprovalError("execution_site_scope_mismatch")
            row = self._row(references.operation_id, lock=True)
            intent = self._intent(references, actor, version, scope, decision)
            saved_intent = row.meta.get("intent") or {key: value for key, value in row.meta["record"].items()
                if key not in {"command", "request_hash", "binding", "before", "after"}}
            if {**intent, "actor_role": saved_intent["actor_role"]} != saved_intent:
                raise ApprovalError("execution_idempotency_conflict")
            preparation = PreparationState.load(row.meta.get("preparation")) if "preparation" in row.meta else None
            if preparation is None or not preparation.live(preparation_lease, self.clock()):
                raise ApprovalError("preparation_lease_conflict")
            if row.meta.get("prepared", True) and int(self.clock().timestamp()) >= row.meta["record"]["command"]["authorization_deadline"]:
                raise ApprovalError("execution_expired")
            if row.meta["state"] not in {"preflight", "dry_run", "ready"}:
                raise ApprovalError("execution_state_conflict")
            command, after = make_command(version.model_dump(mode="json"), references, binding, before)
            command["target_url"] = version.target.url
            command["authorization_deadline"] = int(min(valid_until, self.clock() + timedelta(seconds=60)).timestamp())
            from shared.hitl_execution import bind_proposal_hash
            bind_proposal_hash(command)
            record = {**saved_intent, "binding": binding.model_dump(),
                      "before": before, "after": after, "command": command, "request_hash": digest(command)}
            if row.meta.get("prepared", True):
                # A retry preserves the original bounded command, including its deadline.
                # All other inputs must still match the freshly authorized version.
                command["authorization_deadline"] = row.meta["record"]["command"]["authorization_deadline"]
                bind_proposal_hash(command)
                record["request_hash"] = digest(command)
                if row.meta["record"] != record:
                    raise ApprovalError("execution_idempotency_conflict")
            else:
                row.before_value, row.after_value = before, after
                row.meta = {**row.meta, "record": record, "record_hash": record_hash(record), "prepared": True}
            if row.meta["state"] == "preflight":
                row.meta = {**row.meta, "state": "dry_run", "preparation": preparation.transition("dry_run", self.clock()).dump()}
            self.db.commit()
            return self.serialize(row)
        except IntegrityError:
            self.db.rollback()
            raise ApprovalError("execution_already_exists") from None
        except Exception:
            self.db.rollback()
            raise

    def begin(self, operation_id: str, actor: UserActor, *, preparation_lease: str | None = None) -> dict[str, Any]:
        try:
            initial = self._row(operation_id)
            record = initial.meta["record"]
            references = ExecutionRequest.model_validate({key: record[key] for key in ExecutionRequest.model_fields})
            # _authorize locks Task first, then the enforced Core authority barrier.
            self.db.execute(text("SET LOCAL lock_timeout='5s'"))
            task, _, version, _, decision, valid_until = self._authorize(references, actor)
            row = self._row(operation_id, lock=True)
            if (row.meta["state"] != "ready" or actor.user_id != record["actor_id"]
                    or decision.digest() != record["decision_hash"] or version.model_dump(mode="json") != record["version"]):
                raise ApprovalError("execution_state_conflict")
            if self.clock() >= valid_until or int(self.clock().timestamp()) >= record["command"]["authorization_deadline"]:
                raise ApprovalError("execution_expired")
            now = self.clock()
            if "preparation" not in row.meta or not PreparationState.load(row.meta["preparation"]).live(preparation_lease, now):
                raise ApprovalError("preparation_lease_conflict")
            row.meta = {**row.meta, "state": "applying", "began_at": now.isoformat(),
                        "recovery": RecoveryState.initial(now, record["command"]["authorization_deadline"]).dump()}
            task.status = TaskStatus.IN_PROGRESS
            task.updated_at = self.clock()
            self.db.commit()
            return self.serialize(row)
        except Exception:
            self.db.rollback()
            raise

    def finish(self, operation_id: str, *, receipt: dict[str, Any] | None) -> dict[str, Any]:
        # Service-only completion. Only a checked CMS receipt can say applied_unverified.
        try:
            initial = self._row(operation_id)
            task = cast(TaskRow | None, self.db.query(Task).filter(Task.id == initial.task_id).populate_existing().with_for_update().first())
            if task is None:
                raise ApprovalError("execution_not_found", 404)
            row = self._row(operation_id, lock=True)
            if row.meta["state"] in {"applied_unverified", "no_op"} and (receipt is None or row.meta.get("receipt") == receipt):
                self.db.commit()
                return self.serialize(row)
            if row.meta["state"] not in {"applying", "outcome_unknown", "needs_reconciliation"}:
                raise ApprovalError("execution_state_conflict")
            now = self.clock()
            if receipt is not None:
                self._complete_receipt(row, task, receipt, now)
            elif row.meta["state"] == "applying":
                recovery = RecoveryState.load(row.meta["recovery"])
                recovery = recovery.model_copy(update={"next_attempt_at": min(recovery.next_attempt_at or now, now)})
                row.meta = {**row.meta, "state": "outcome_unknown", "recovery": recovery.dump(), "finished_at": now.isoformat()}
            self.db.commit()
            return self.serialize(row)
        except Exception:
            self.db.rollback()
            raise

    @staticmethod
    def _complete_receipt(row: OperationRow, task: TaskRow, receipt: dict[str, Any], now: datetime) -> None:
        record = row.meta["record"]
        validate_receipt(receipt, record["command"], record["before"], record["after"])
        metadata = dict(row.meta)
        if "recovery" in metadata:
            recovery = RecoveryState.load(metadata["recovery"])
            if recovery.attempts and recovery.attempts[-1].status == "pending":
                recovery = recovery.settled(now, "superseded")
            metadata["recovery"] = recovery.model_copy(update={"next_attempt_at": None}).dump()
        row.applied = receipt["outcome"] == "applied_unverified"
        task.status, task.updated_at = TaskStatus.DEPLOYED, now
        row.meta = {**metadata, "state": receipt["outcome"], "receipt": receipt, "finished_at": now.isoformat()}
