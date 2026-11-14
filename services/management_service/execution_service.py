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
from services.management_service.verification_policy import VerificationState
from shared.hitl_rollback import RollbackRequest, bind_rollback_hash, make_rollback_command
from shared.hitl_verification import PublicBaseline, PublicObservation

SOURCE = "hitl_executor_v1"
ENTITY = "deployment_operation"
CONFIRMED_STATES = frozenset({"applied_unverified", "no_op", "verifying", "verified", "verification_failed",
    "rollback_applied_unverified", "rollback_verifying", "rollback_verified", "rollback_verification_failed", "rollback_in_progress", "rolled_back"})


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
    def __init__(self, db: Session, *, clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc), fresh_days: int = 30,
                 verification_deadline_seconds: int = 300):
        self.db, self.clock = db, clock
        self.approvals = ApprovalService(db, clock=clock)
        self.fresh_days = fresh_days
        self.verification_deadline_seconds = verification_deadline_seconds

    def _authorize(self, references: ExecutionRequest, actor: UserActor) -> tuple[TaskRow, ApprovalRow, RecommendationVersion, Mapping[str, Any], DecisionRecord, datetime]:
        task = cast(TaskRow | None, self.db.query(Task).filter(Task.id == references.task_id).populate_existing().with_for_update().first())
        if task is None:
            raise ApprovalError("execution_not_found", 404)
        scope = self.approvals.project_scope(str(task.project_id), actor, Permission.DEPLOY)
        self._no_other_inflight(str(task.id), references.operation_id)
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
        valid_until = min(decision.expires_at, self._ownership_deadline(version, scope))
        if self.clock() >= valid_until:
            raise ApprovalError("execution_expired")
        return task, approval, version, scope, decision, valid_until

    def _ownership_deadline(self, version: RecommendationVersion, scope: Mapping[str, Any]) -> datetime:
        verified = self.db.execute(text(
            "SELECT verified_at FROM audit_schema.domain_authorizations WHERE project_id=:project AND workspace_id=:workspace "
            "AND status='verified' AND scope_origin=:origin AND scope_path_prefix=:prefix "
            "AND revoked_at IS NULL"
        ), {"project": version.project_id, "workspace": scope["workspace_id"], "origin": scope["origin"],
            "prefix": scope["path_prefix"]}).scalar()
        if verified is None or not is_fresh("verified", verified, self.fresh_days, self.clock()):
            raise ApprovalError("execution_domain_authorization_required")
        return cast(datetime, verified + timedelta(days=self.fresh_days))

    def _no_other_inflight(self, task_id: str, operation_id: str) -> None:
        if self.db.scalar(text("""SELECT EXISTS(SELECT 1 FROM public.management_changelog
          WHERE source=:source AND task_id=:task AND id!=:operation
          AND metadata->>'state' IN ('applying','outcome_unknown','needs_reconciliation','rollback_in_progress'))"""),
          {"source": SOURCE, "task": task_id, "operation": operation_id}):
            raise ApprovalError("execution_other_operation_inflight")

    def _authorize_rollback(self, record: dict[str, Any], actor: UserActor) -> tuple[TaskRow, RecommendationVersion, Mapping[str, Any], datetime]:
        task = cast(TaskRow | None, self.db.query(Task).filter(Task.id == record["task_id"]).populate_existing().with_for_update().first())
        if task is None:
            raise ApprovalError("execution_not_found", 404)
        scope = self.approvals.project_scope(str(task.project_id), actor, Permission.DEPLOY)
        self._no_other_inflight(str(task.id), record["operation_id"])
        original = self._row(record["original_operation_id"], lock=True)
        if (original.meta.get("operation_type", "apply") != "apply" or str(original.task_id) != str(task.id)
                or original.meta["record"]["project_id"] != str(task.project_id)
                or original.meta["state"] not in {"applied_unverified", "no_op", "verifying", "verified", "verification_failed"}
                or original.meta.get("rollback_operation_id")):
            raise ApprovalError("rollback_original_unavailable")
        source = original.meta["record"]
        receipt = original.meta.get("receipt")
        if not isinstance(receipt, dict):
            raise ApprovalError("rollback_original_unavailable")
        validate_receipt(receipt, source["command"], source["before"], source["after"])
        version = RecommendationVersion.model_validate(source["version"])
        if (record["actor_id"] != actor.user_id or record["workspace_id"] != scope["workspace_id"]
                or scope["status"] != "active" or scope["origin"] != version.target.origin
                or not parse_scope(scope["origin"] + scope["path_prefix"]).contains(version.target.url)
                or record["original_record_hash"] != original.meta["record_hash"]
                or record["original_receipt_id"] != receipt["receipt_id"]
                or record["original_request_hash"] != source["request_hash"]
                or record["original_proposal_hash"] != source["command"]["proposal_hash"]
                or record["restore_before"] != source["before"] or record["expected_current"] != source["after"]
                or record["original_binding"] != source["binding"] or record["version"] != source["version"]):
            raise ApprovalError("rollback_reference_conflict")
        return task, version, scope, self._ownership_deadline(version, scope)

    def _authorize_operation(self, references: ExecutionRequest, actor: UserActor, row: OperationRow) -> tuple[TaskRow, RecommendationVersion, Mapping[str, Any], dict[str, Any], datetime]:
        if any(row.meta["record"].get(key) != value for key, value in references.model_dump().items()):
            raise ApprovalError("execution_idempotency_conflict")
        if row.meta.get("operation_type") == "rollback":
            task, version, scope, until = self._authorize_rollback(row.meta["record"], actor)
            saved = row.meta["intent"]
            return task, version, scope, saved, until
        task, _, version, scope, decision, until = self._authorize(references, actor)
        return task, version, scope, self._intent(references, actor, version, scope, decision), until

    def queue_rollback(self, original_id: str, request: RollbackRequest, actor: UserActor) -> dict[str, Any]:
        try:
            original = self._row(original_id)
            task = cast(TaskRow | None, self.db.query(Task).filter(Task.id == original.task_id).populate_existing().with_for_update().first())
            if task is None:
                raise ApprovalError("execution_not_found", 404)
            scope = self.approvals.project_scope(str(original.project_id), actor, Permission.DEPLOY)
            original = self._row(original_id, lock=True)
            existing = self.db.query(Changelog).filter(Changelog.id == request.operation_id).first()
            if existing is not None:
                row = self._row(request.operation_id, lock=True)
                if (row.meta.get("operation_type") != "rollback" or row.meta["record"].get("original_operation_id") != original_id
                        or row.meta["record"].get("reason") != request.reason or row.meta["record"]["actor_id"] != actor.user_id):
                    raise ApprovalError("execution_idempotency_conflict")
                self.db.rollback()
                return self.serialize(row)
            source = original.meta["record"]
            receipt = original.meta.get("receipt")
            if not isinstance(receipt, dict) or original.meta.get("operation_type", "apply") != "apply":
                raise ApprovalError("rollback_original_unavailable")
            refs = ExecutionRequest(operation_id=request.operation_id, task_id=source["task_id"], approval_id=source["approval_id"],
                recommendation_version_id=source["recommendation_version_id"], content_hash=source["content_hash"])
            intent = {**refs.model_dump(), "operation_type": "rollback", "project_id": source["project_id"], "workspace_id": scope["workspace_id"],
                "actor_id": actor.user_id, "actor_role": scope["role"], "version": source["version"], "reason": request.reason,
                "original_operation_id": original_id, "original_record_hash": original.meta["record_hash"],
                "original_receipt_id": receipt["receipt_id"], "original_request_hash": source["request_hash"],
                "original_proposal_hash": source["command"]["proposal_hash"], "original_binding": source["binding"],
                "restore_before": source["before"], "expected_current": source["after"]}
            intent["decision_hash"] = digest(intent)  # Explicit rollback consent; old approval is a historic reference only.
            self._authorize_rollback(intent, actor)
            metadata = {"operation_type": "rollback", "intent": intent, "intent_hash": record_hash(intent), "record": intent,
                "record_hash": record_hash(intent), "prepared": False, "state": "queued", "preparation": PreparationState.initial(self.clock()).dump()}
            if "public_baseline" in original.meta:
                metadata["public_baseline"] = original.meta["public_baseline"]
            row = cast(OperationRow, Changelog(id=UUID(request.operation_id), task_id=task.id, project_id=task.project_id,
                entity_id=request.operation_id, entity_type=ENTITY, change_type="execute_v1", source=SOURCE, applied=False,
                before_value=null(), after_value=null(), meta=metadata))
            self.db.add(row)
            self.db.commit()
            return self.serialize(row)
        except IntegrityError:
            self.db.rollback()
            raise ApprovalError("rollback_already_exists") from None
        except Exception:
            self.db.rollback()
            raise

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
                "operation_type": row.meta.get("operation_type", "apply"),
                "rollback_operation_id": row.meta.get("rollback_operation_id"),
                "public_baseline": row.meta.get("public_baseline"),
                "verification": VerificationState.load(row.meta["verification"]).summary() if "verification" in row.meta else None,
                "preparation": PreparationState.load(row.meta["preparation"]).summary() if "preparation" in row.meta else None,
                "recovery": RecoveryState.load(row.meta["recovery"]).summary() if "recovery" in row.meta else None}

    def read(self, operation_id: str, actor: UserActor) -> dict[str, Any]:
        row = self._row(operation_id)
        self.approvals.project_scope(str(row.project_id), actor, Permission.READ)
        return self.serialize(row)

    def reserve(self, references: ExecutionRequest, actor: UserActor, binding: WordPressBinding, before: Any,
                *, preparation_lease: str | None = None, public_baseline: PublicObservation | None = None) -> dict[str, Any]:
        try:
            initial = self._row(references.operation_id)
            _, version, _, intent, valid_until = self._authorize_operation(references, actor, initial)
            if parse_scope(binding.base_url).origin != version.target.origin:
                raise ApprovalError("execution_site_scope_mismatch")
            row = self._row(references.operation_id, lock=True)
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
            rollback = row.meta.get("operation_type") == "rollback"
            if rollback:
                command, after = make_rollback_command(saved_intent, binding, before)
            else:
                command, after = make_command(version.model_dump(mode="json"), references, binding, before)
            command["target_url"] = version.target.url
            command["authorization_deadline"] = int(min(valid_until, self.clock() + timedelta(seconds=60)).timestamp())
            from shared.hitl_execution import bind_proposal_hash
            binder = bind_rollback_hash if rollback else bind_proposal_hash
            binder(command)
            record = {**saved_intent, "binding": binding.model_dump(),
                      "before": before, "after": after, "command": command, "request_hash": digest(command)}
            if row.meta.get("prepared", True):
                # A retry preserves the original bounded command, including its deadline.
                # All other inputs must still match the freshly authorized version.
                command["authorization_deadline"] = row.meta["record"]["command"]["authorization_deadline"]
                binder(command)
                record["request_hash"] = digest(command)
                if row.meta["record"] != record:
                    raise ApprovalError("execution_idempotency_conflict")
            else:
                row.before_value, row.after_value = before, after
                row.meta = {**row.meta, "record": record, "record_hash": record_hash(record), "prepared": True}
            if row.meta["state"] == "preflight":
                row.meta = {**row.meta, "state": "dry_run", "preparation": preparation.transition("dry_run", self.clock()).dump()}
            if not rollback and "public_baseline" not in row.meta and public_baseline is not None:
                if (public_baseline.url != version.target.url or public_baseline.final_url != version.target.url
                        or public_baseline.status != 200 or public_baseline.facts is None
                        or public_baseline.observed_at > self.clock()
                        or public_baseline.observed_at < preparation.started_at):
                    raise ApprovalError("verification_baseline_invalid")
                baseline = PublicBaseline(operation_id=references.operation_id, project_id=version.project_id,
                    recommendation_version_id=version.version_id, content_hash=version.content_hash, observation=public_baseline)
                row.meta = {**row.meta, "public_baseline": baseline.model_dump(mode="json")}
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
            task, version, _, intent, valid_until = self._authorize_operation(references, actor, initial)
            row = self._row(operation_id, lock=True)
            if (row.meta["state"] != "ready" or actor.user_id != record["actor_id"]
                    or intent["decision_hash"] != record["decision_hash"] or version.model_dump(mode="json") != record["version"]):
                raise ApprovalError("execution_state_conflict")
            if self.clock() >= valid_until or int(self.clock().timestamp()) >= record["command"]["authorization_deadline"]:
                raise ApprovalError("execution_expired")
            now = self.clock()
            if "preparation" not in row.meta or not PreparationState.load(row.meta["preparation"]).live(preparation_lease, now):
                raise ApprovalError("preparation_lease_conflict")
            row.meta = {**row.meta, "state": "applying", "began_at": now.isoformat(),
                        "recovery": RecoveryState.initial(now, record["command"]["authorization_deadline"]).dump()}
            if row.meta.get("operation_type") == "rollback":
                self.db.flush()
                original = self._row(record["original_operation_id"], lock=True)
                metadata = dict(original.meta)
                if "verification" in metadata:
                    metadata["verification"] = VerificationState.load(metadata["verification"]).superseded(now).dump()
                original.meta = {**metadata, "rollback_operation_id": operation_id, "state": "rollback_in_progress"}
            approval = cast(ApprovalRow | None, self.db.query(HITLApproval).filter(HITLApproval.task_id == task.id).populate_existing().first())
            current_version = ((approval.diff_data if approval else {}) or {}).get("recommendation_version", {}).get("version_id")
            if current_version == record["recommendation_version_id"]:
                task.status, task.updated_at = TaskStatus.IN_PROGRESS, now
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
            if row.meta["state"] in CONFIRMED_STATES and (receipt is None or row.meta.get("receipt") == receipt):
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

    def _complete_receipt(self, row: OperationRow, task: TaskRow, receipt: dict[str, Any], now: datetime) -> None:
        record = row.meta["record"]
        validate_receipt(receipt, record["command"], record["before"], record["after"])
        metadata = dict(row.meta)
        if "recovery" in metadata:
            recovery = RecoveryState.load(metadata["recovery"])
            if recovery.attempts and recovery.attempts[-1].status == "pending":
                recovery = recovery.settled(now, "superseded")
            metadata["recovery"] = recovery.model_copy(update={"next_attempt_at": None}).dump()
        rollback = row.meta.get("operation_type") == "rollback"
        approval = cast(ApprovalRow | None, self.db.query(HITLApproval).filter(HITLApproval.task_id == task.id).populate_existing().first())
        current_version = ((approval.diff_data if approval else {}) or {}).get("recommendation_version", {}).get("version_id")
        row.applied = receipt["outcome"] in {"applied_unverified", "rollback_applied_unverified"}
        if current_version == record["recommendation_version_id"]:
            task.status, task.updated_at = TaskStatus.DEPLOYED, now
        state = "rollback_applied_unverified" if rollback else receipt["outcome"]
        row.meta = {**metadata, "state": state, "receipt": receipt, "finished_at": now.isoformat(),
                    "verification": VerificationState.initial(now, self.verification_deadline_seconds).dump()}
        if rollback:
            original = self._row(record["original_operation_id"], lock=True)
            original.meta = {**original.meta, "state": "rolled_back"}
