"""Leased status reconciliation, sharing the existing Task → operation lock order."""
from datetime import datetime, timezone
from typing import Any, Callable, cast

from sqlalchemy import text
from sqlalchemy.orm import Session

from services.management_service.approval_policy import ApprovalError
from services.management_service.approval_service import TaskRow
from services.management_service.execution_service import CONFIRMED_STATES, ENTITY, SOURCE, ExecutionService, OperationRow
from services.management_service.models import Task
from services.management_service.recovery_policy import RecoveryState
from shared.hitl_execution import ExecutionError, RecoveryReport, validate_receipt


class ExecutionRecovery:
    def __init__(self, db: Session, *, clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc), verification_deadline_seconds: int = 300):
        self.db, self.clock = db, clock
        self.executions = ExecutionService(db, clock=clock, verification_deadline_seconds=verification_deadline_seconds)

    def _locked(self, operation_id: str, *, skip_locked: bool = False) -> tuple[TaskRow, OperationRow] | None:
        initial = self.executions._row(operation_id)
        task = cast(TaskRow | None, self.db.query(Task).filter(Task.id == initial.task_id)
                    .populate_existing().with_for_update(skip_locked=skip_locked).first())
        if task is None:
            if skip_locked:
                return None
            raise ApprovalError("execution_not_found", 404)
        row = self.executions._row(operation_id, lock=True)
        return task, row

    def claim(self, operation_id: str | None = None) -> dict[str, Any]:
        result: dict[str, Any]
        try:
            candidates = [operation_id] if operation_id else list(self.db.scalars(text(
                "SELECT id::text FROM public.management_changelog "
                "WHERE source=:source AND entity_type=:entity "
                "AND metadata->>'state' IN ('applying','outcome_unknown') AND "
                "((metadata->'recovery'->>'next_attempt_at')::timestamptz <= CURRENT_TIMESTAMP "
                "OR (metadata->'recovery'->>'deadline_at')::timestamptz <= CURRENT_TIMESTAMP) "
                "ORDER BY created_at,id LIMIT 20"
            ), {"source": SOURCE, "entity": ENTITY}))
            for candidate in candidates:
                locked = self._locked(candidate, skip_locked=True)
                if locked is None:
                    self.db.rollback()
                    continue
                _, row = locked
                if row.meta["state"] not in {"applying", "outcome_unknown"}:
                    result = {"operation": self.executions.serialize(row), "lease_id": None}
                    self.db.rollback()
                    if operation_id:
                        return result
                    continue
                recovery = RecoveryState.load(row.meta.get("recovery"))
                # Fresh clock AFTER all row locks; waiting cannot extend a lease/deadline.
                updated, token = recovery.claim(self.clock())
                if updated != recovery:
                    row.meta = {**row.meta, "recovery": updated.dump(),
                        "state": "needs_reconciliation" if updated.stop_reason else "outcome_unknown"}
                result = dict(operation=self.executions.serialize(row), lease_id=token)
                self.db.commit()  # Claim/attempt is durable BEFORE Client performs HTTP.
                if operation_id or token:
                    return result
            return {"operation": None, "lease_id": None}
        except Exception:
            self.db.rollback()
            raise

    def report(self, operation_id: str, report: RecoveryReport) -> dict[str, Any]:
        try:
            locked = self._locked(operation_id)
            if locked is None:
                raise ApprovalError("execution_not_found", 404)
            task, row = locked
            if row.meta["state"] in CONFIRMED_STATES and report.receipt == row.meta.get("receipt"):
                self.db.rollback()
                return self.executions.serialize(row)
            recovery = RecoveryState.load(row.meta.get("recovery"))
            now = self.clock()
            if (row.meta["state"] not in {"applying", "outcome_unknown"} or not recovery.attempts
                    or recovery.attempts[-1].status != "pending"
                    or recovery.attempts[-1].lease_id != report.lease_id
                    or now >= recovery.attempts[-1].lease_until or now >= recovery.deadline_at):
                raise ApprovalError("recovery_lease_conflict")
            error = report.error_code
            if report.receipt is not None:
                try:
                    record = row.meta["record"]
                    validate_receipt(report.receipt, record["command"], record["before"], record["after"])
                except (ExecutionError, ValueError, TypeError, KeyError):
                    error = "receipt_invalid"
            updated = recovery.reported(now, error)
            row.meta = {**row.meta, "recovery": updated.dump(),
                "state": "needs_reconciliation" if updated.stop_reason else "outcome_unknown"}
            if error is None and report.receipt is not None:
                self.executions._complete_receipt(row, task, report.receipt, now)
            self.db.commit()
            return self.executions.serialize(row)
        except Exception:
            self.db.rollback()
            raise
