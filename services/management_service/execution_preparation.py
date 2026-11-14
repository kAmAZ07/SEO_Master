"""Authoritative pre-dispatch phases, serialized with begin/cancel on Task first."""
from datetime import datetime, timezone
from typing import Any, Callable, cast

from sqlalchemy import text
from sqlalchemy.orm import Session

from services.management_service.approval_policy import ApprovalError, UserActor
from services.management_service.approval_service import TaskRow
from services.management_service.execution_service import ExecutionService, OperationRow
from services.management_service.models import Task
from services.management_service.preparation_policy import FAILED_PHASES, MAX_ATTEMPTS, OPEN_PHASES, PreparationState, failure_phase
from shared.hitl_execution import ExecutionRequest, PreparationError, PreparationFailure
from shared.projects.roles import Permission


class ExecutionPreparation:
    def __init__(self, db: Session, *, clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc), fresh_days: int = 30):
        self.db, self.clock = db, clock
        self.executions = ExecutionService(db, clock=clock, fresh_days=fresh_days)

    def _locked(self, operation_id: str) -> tuple[TaskRow, OperationRow]:
        self.db.execute(text("SET LOCAL lock_timeout='5s'"))
        initial = self.executions._row(operation_id)
        task = cast(TaskRow | None, self.db.query(Task).filter(Task.id == initial.task_id).populate_existing().with_for_update().first())
        if task is None:
            raise ApprovalError("execution_not_found", 404)
        return task, self.executions._row(operation_id, lock=True)

    @staticmethod
    def _fail(row: OperationRow, value: PreparationState, now: datetime, error: PreparationError) -> None:
        phase = failure_phase(error)
        row.meta = {**row.meta, "state": phase, "preparation": value.transition(phase, now, error).dump()}

    def claim(self, operation_id: str, actor: UserActor) -> dict[str, Any]:
        result: dict[str, Any]
        try:
            _, row = self._locked(operation_id)
            self.executions.approvals.project_scope(str(row.project_id), actor, Permission.READ)
            if row.meta["record"]["actor_id"] != actor.user_id:
                raise ApprovalError("execution_actor_mismatch", 403)
            if row.meta["state"] not in OPEN_PHASES:
                result = {"operation": self.executions.serialize(row), "lease_id": None}
                self.db.rollback()
                return result
            now = self.clock()  # Fresh after Task/op/authority waits.
            value = PreparationState.load(row.meta["preparation"]) if "preparation" in row.meta else PreparationState.initial(now, "ready")
            error: PreparationError | None = None
            if now >= value.deadline_at or value.attempts >= MAX_ATTEMPTS:
                if value.live(value.lease_id, now):
                    raise ApprovalError("execution_preparation_busy")
                error = "preparation_expired"
            elif row.meta.get("prepared", True) and int(now.timestamp()) >= row.meta["record"]["command"]["authorization_deadline"]:
                error = "authorization_expired"
            elif value.live(value.lease_id, now):
                raise ApprovalError("execution_preparation_busy")
            if error is None:
                refs = ExecutionRequest.model_validate({key: row.meta["record"][key] for key in ExecutionRequest.model_fields})
                try:
                    _, _, _, current, _ = self.executions._authorize_operation(refs, actor, row)
                    saved = row.meta.get("intent") or {key: item for key, item in row.meta["record"].items()
                        if key not in {"command", "request_hash", "binding", "before", "after"}}
                    if {**current, "actor_role": saved["actor_role"]} != saved:
                        raise ApprovalError("execution_revision_conflict")
                except ApprovalError as exc:
                    error = "authorization_expired" if "expired" in exc.code else "authorization_rejected"
            now = self.clock()
            if error is None and now >= value.deadline_at:
                error = "preparation_expired"
            if (error is None and row.meta.get("prepared", True)
                    and int(now.timestamp()) >= row.meta["record"]["command"]["authorization_deadline"]):
                error = "authorization_expired"
            if error is not None:
                self._fail(row, value, now, error)
                token = None
            else:
                value = value.claimed(now)
                token = value.lease_id
                row.meta = {**row.meta, "state": "preflight", "preparation": value.dump()}
            result = dict(operation=self.executions.serialize(row), lease_id=token)
            self.db.commit()  # Persist lease/phase BEFORE any CMS I/O.
            return result
        except Exception:
            self.db.rollback()
            raise

    def ready(self, operation_id: str, token: str) -> dict[str, Any]:
        try:
            _, row = self._locked(operation_id)
            value = PreparationState.load(row.meta.get("preparation"))
            now = self.clock()
            if not value.live(token, now) or row.meta["state"] not in {"dry_run", "ready"} or row.meta.get("prepared") is False:
                raise ApprovalError("preparation_lease_conflict")
            if int(now.timestamp()) >= row.meta["record"]["command"]["authorization_deadline"]:
                self._fail(row, value, now, "authorization_expired")
            elif row.meta["state"] != "ready":
                row.meta = {**row.meta, "state": "ready", "preparation": value.transition("ready", now).dump()}
            self.db.commit()
            return self.executions.serialize(row)
        except Exception:
            self.db.rollback()
            raise

    def fail(self, operation_id: str, report: PreparationFailure) -> dict[str, Any]:
        try:
            _, row = self._locked(operation_id)
            value = PreparationState.load(row.meta.get("preparation"))
            now = self.clock()
            if (row.meta["state"] in FAILED_PHASES and value.lease_id == report.lease_id
                    and value.events[-1].error_code == report.error_code):
                self.db.rollback()
                return self.executions.serialize(row)
            if row.meta["state"] not in OPEN_PHASES or not value.live(report.lease_id, now):
                raise ApprovalError("preparation_lease_conflict")
            self._fail(row, value, now, report.error_code)
            # A readonly failure never changes Task; another approval/version may exist.
            self.db.commit()
            return self.executions.serialize(row)
        except Exception:
            self.db.rollback()
            raise

    def cancel(self, operation_id: str, actor: UserActor) -> dict[str, Any]:
        try:
            _, row = self._locked(operation_id)
            self.executions.approvals.project_scope(str(row.project_id), actor, Permission.DEPLOY)
            if row.meta["state"] == "cancelled":
                self.db.rollback()
                return self.executions.serialize(row)
            if row.meta["state"] not in OPEN_PHASES | FAILED_PHASES:
                raise ApprovalError("execution_state_conflict")
            now = self.clock()
            value = PreparationState.load(row.meta["preparation"]) if "preparation" in row.meta else PreparationState.initial(now, "ready")
            row.meta = {**row.meta, "state": "cancelled", "preparation": value.transition("cancelled", now).dump(),
                        "cancel_actor_id": actor.user_id, "cancelled_at": now.isoformat(), "cancel_reason": "user_request"}
            self.db.commit()
            return self.executions.serialize(row)
        except Exception:
            self.db.rollback()
            raise
