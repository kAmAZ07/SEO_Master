"""Read-only public evidence is committed under the operation's durable lease."""
from datetime import datetime, timezone
from typing import Any, Callable, cast

from sqlalchemy import text
from sqlalchemy.orm import Session

from services.management_service.approval_policy import ApprovalError
from services.management_service.approval_service import ApprovalRow, TaskRow
from services.management_service.execution_service import ENTITY, SOURCE, ExecutionService, OperationRow
from services.management_service.models import HITLApproval, Task, TaskStatus
from services.management_service.verification_policy import VerificationState
from shared.hitl_verification import PublicBaseline, VerificationReport, VerificationResult, evaluate_public

ELIGIBLE = frozenset({"applied_unverified", "no_op", "verifying", "rollback_applied_unverified", "rollback_verifying"})


def phase(value: VerificationState, rollback: bool) -> str:
    if value.status == "matched":
        return "rollback_verified" if rollback else "verified"
    if value.status == "failed":
        return "rollback_verification_failed" if rollback else "verification_failed"
    return "rollback_verifying" if rollback else "verifying"


class ExecutionVerification:
    def __init__(self, db: Session, *, clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc), deadline_seconds: int = 300):
        self.db, self.clock, self.deadline_seconds = db, clock, deadline_seconds
        self.executions = ExecutionService(db, clock=clock)

    def _locked(self, operation_id: str, *, skip_locked: bool = False) -> tuple[TaskRow, OperationRow] | None:
        self.db.execute(text("SET LOCAL lock_timeout='5s'"))
        initial = self.executions._row(operation_id)
        task = cast(TaskRow | None, self.db.query(Task).filter(Task.id == initial.task_id).populate_existing()
                    .with_for_update(skip_locked=skip_locked).first())
        return (task, self.executions._row(operation_id, lock=True)) if task is not None else None

    def _value(self, row: OperationRow) -> VerificationState:
        if "verification" in row.meta:
            return VerificationState.load(row.meta["verification"])
        receipt = row.meta.get("receipt")
        if not isinstance(receipt, dict):
            raise ApprovalError("verification_receipt_required")
        # Existing records never receive a new deadline at restart or migration.
        started = datetime.fromisoformat(row.meta.get("finished_at") or receipt["applied_at"].replace("Z", "+00:00"))
        return VerificationState.initial(started, self.deadline_seconds)

    def claim(self, operation_id: str | None = None) -> dict[str, Any]:
        result: dict[str, Any]
        try:
            candidates = [operation_id] if operation_id else list(self.db.scalars(text(
                "SELECT id::text FROM public.management_changelog WHERE source=:source AND entity_type=:entity "
                "AND metadata->>'state' = ANY(:states) AND (NOT metadata ? 'verification' OR "
                "(metadata->'verification'->>'next_attempt_at')::timestamptz <= CURRENT_TIMESTAMP OR "
                "(metadata->'verification'->>'deadline_at')::timestamptz <= CURRENT_TIMESTAMP) ORDER BY created_at,id LIMIT 20"
            ), {"source": SOURCE, "entity": ENTITY, "states": list(ELIGIBLE)}))
            for candidate in candidates:
                locked = self._locked(candidate, skip_locked=True)
                if locked is None:
                    self.db.rollback()
                    continue
                _, row = locked
                if row.meta["state"] not in ELIGIBLE or row.meta.get("rollback_operation_id"):
                    result = {"operation": self.executions.serialize(row), "lease_id": None}
                    self.db.rollback()
                    if operation_id:
                        return result
                    continue
                value = self._value(row)
                updated, token = value.claim(self.clock())
                if updated != value or "verification" not in row.meta:
                    row.meta = {**row.meta, "verification": updated.dump(),
                                "state": phase(updated, row.meta.get("operation_type") == "rollback")}
                result = dict(operation=self.executions.serialize(row), lease_id=token)
                self.db.commit()  # One attempt/lease is durable BEFORE public HTTP.
                if operation_id or token:
                    return result
            return {"operation": None, "lease_id": None}
        except Exception:
            self.db.rollback()
            raise

    def report(self, operation_id: str, report: VerificationReport) -> dict[str, Any]:
        try:
            locked = self._locked(operation_id)
            if locked is None:
                raise ApprovalError("execution_not_found", 404)
            task, row = locked
            value = self._value(row)
            now = self.clock()  # Waiting for Task/op locks cannot renew a lease.
            if (row.meta["state"] not in ELIGIBLE or row.meta.get("rollback_operation_id") or not value.attempts
                    or value.status != "pending" or value.attempts[-1].status != "pending"
                    or value.attempts[-1].lease_id != report.lease_id or now >= value.attempts[-1].lease_until or now >= value.deadline_at):
                raise ApprovalError("verification_lease_conflict")
            supplied = report.result
            if supplied.observation is not None:
                observation = supplied.observation
                if observation.observed_at < value.attempts[-1].started_at or observation.observed_at > now:
                    raise ApprovalError("verification_observation_time_invalid")
                baseline = PublicBaseline.load(row.meta["public_baseline"]).observation if "public_baseline" in row.meta else None
                # Caller flags cannot substitute the saved version's predicates.
                checked = evaluate_public(row.meta["record"], observation, baseline)
            else:
                checked = VerificationResult(error_code=supplied.error_code or "fetch_failed")
            updated = value.reported(now, checked)
            row.meta = {**row.meta, "verification": updated.dump(), "state": phase(updated, row.meta.get("operation_type") == "rollback")}
            if updated.status == "matched":
                approval = cast(ApprovalRow | None, self.db.query(HITLApproval).filter(HITLApproval.task_id == task.id).populate_existing().first())
                current_version = ((approval.diff_data if approval else {}) or {}).get("recommendation_version", {}).get("version_id")
                if (current_version == row.meta["record"]["recommendation_version_id"]
                        and task.status in {TaskStatus.DEPLOYED, TaskStatus.IN_PROGRESS}):
                    task.status = TaskStatus.CANCELLED if row.meta.get("operation_type") == "rollback" else TaskStatus.COMPLETED
                    task.updated_at = now
            self.db.commit()
            return self.executions.serialize(row)
        except Exception:
            self.db.rollback()
            raise
