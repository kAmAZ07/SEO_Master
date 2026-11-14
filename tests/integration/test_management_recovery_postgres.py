"""AR-6 persisted leases and safety fences on the actual PostgreSQL migration line."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from threading import Barrier
import time
from uuid import UUID

from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from services.management_service.approval_policy import ApprovalError, UserActor
from services.management_service.execution_recovery import ExecutionRecovery
from services.management_service.execution_service import ExecutionService
from services.management_service.migrations.runner import migrate
from services.management_service.models import Changelog, Task, TaskStatus
from shared.hitl_execution import RecoveryReport, digest
from tests.integration.test_management_approvals_postgres import ALICE, NOW
from tests.integration.test_management_execution_postgres import executable as execution_fixture, begin, reserve
from tests.integration.test_management_execution_postgres import stand as approval_fixture
from tests.integration.test_migrations_postgres import ADMIN_URL

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="TEST_PG_ADMIN_URL is not set")
executable = execution_fixture
stand = approval_fixture


def claim(engine, refs, seconds=0):
    with Session(engine) as db:
        return ExecutionRecovery(db, clock=lambda: NOW + timedelta(seconds=seconds)).claim(refs.operation_id)


def report(engine, refs, token, seconds=0, error="cms_unavailable", receipt=None):
    with Session(engine) as db:
        return ExecutionRecovery(db, clock=lambda: NOW + timedelta(seconds=seconds)).report(refs.operation_id,
            RecoveryReport(lease_id=token, error_code=error, receipt=receipt))


def unknown(engine, refs):
    reserve(engine, refs)
    begin(engine, refs)
    with Session(engine) as db:
        return ExecutionService(db, clock=lambda: NOW).finish(refs.operation_id, receipt=None)


def receipt_for(value):
    import hashlib
    record = value["record"]
    cmd, before, after = record["command"], record["before"], record["after"]
    receipt = {key: cmd[key] for key in ("operation_id", "project_id", "site_id", "entity_id", "task_id", "proposal_version", "proposal_hash", "approval_ref", "before_digest")}
    receipt.update(protocol="seo-master/v2", kind="receipt", request_hash=record["request_hash"], after_digest=digest(after),
        before=before, after=after, plugin_version="0.3.0-dev", key_id="test-key", applied_at="2026-11-09T12:00:00Z",
        applied_fields=[item["field"] for item in cmd["changes"] if before["fields"][item["field"]] != after["fields"][item["field"]]],
        outcome="applied_unverified", warnings=[], signature="a" * 64,
        receipt_id=hashlib.sha256(("SEO-MASTER-RECEIPT-ID-V2\n" + cmd["operation_id"] + "\n" + record["request_hash"]).encode()).hexdigest())
    return receipt


def test_only_one_concurrent_claim_spends_one_attempt(executable):
    engine, _, _, refs = executable
    unknown(engine, refs)
    barrier = Barrier(2)
    def concurrent(_):
        barrier.wait(timeout=5)
        return claim(engine, refs)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(concurrent, range(2)))
    assert sum(value["lease_id"] is not None for value in results) == 1
    value = claim(engine, refs)
    assert value["lease_id"] is None and value["operation"]["recovery"]["attempts"] == 1


def test_budget_backoff_manual_fence_and_late_none_survive_new_sessions(executable):
    engine, _, _, refs = executable
    frozen = unknown(engine, refs)
    for number, seconds in enumerate((0, 10, 70, 370), 1):
        value = claim(engine, refs, seconds)
        assert value["lease_id"] and value["operation"]["recovery"]["attempts"] == number
        result = report(engine, refs, value["lease_id"], seconds, error="operation_not_found")
        if number < 4:
            assert claim(engine, refs, seconds)["lease_id"] is None
    assert result["state"] == "needs_reconciliation"
    assert result["record"] == frozen["record"] and result["record_hash"] == frozen["record_hash"]
    assert result["recovery"]["stop_reason"] == "attempts_exhausted"
    with Session(engine) as db:
        service = ExecutionService(db, clock=lambda: NOW + timedelta(seconds=400))
        assert service.finish(refs.operation_id, receipt=None) == result
        assert db.get(Task, UUID(refs.task_id)).status == TaskStatus.IN_PROGRESS
        with pytest.raises(ApprovalError):
            service.begin(refs.operation_id, UserActor(ALICE))
    with pytest.raises(DBAPIError, match="HITL_EXECUTION_INVALID_TRANSITION"):
        with engine.begin() as conn:
            conn.execute(text("UPDATE public.management_changelog SET metadata=jsonb_set(metadata,'{state}','\"ready\"')"))
    assert claim(engine, refs, 2000)["lease_id"] is None


def test_crash_expired_lease_spends_attempt_and_stale_report_cannot_change_journal(executable):
    engine, _, _, refs = executable
    unknown(engine, refs)
    first = claim(engine, refs)
    assert claim(engine, refs, 59)["lease_id"] is None
    expired = claim(engine, refs, 60)
    assert expired["lease_id"] is None
    second = claim(engine, refs, 70)
    assert second["lease_id"] and second["lease_id"] != first["lease_id"]
    with pytest.raises(ApprovalError, match="recovery_lease_conflict"):
        report(engine, refs, first["lease_id"], 71)
    assert claim(engine, refs, 71)["operation"] == second["operation"]
    result = report(engine, refs, second["lease_id"], 71)
    assert result["recovery"]["attempts"] == 2


def test_deadline_without_status_attempt_goes_to_manual(executable):
    engine, _, _, refs = executable
    reserve(engine, refs)
    begin(engine, refs)
    result = claim(engine, refs, 900)
    assert result["lease_id"] is None and result["operation"]["state"] == "needs_reconciliation"
    assert result["operation"]["recovery"]["attempts"] == 0
    assert result["operation"]["recovery"]["stop_reason"] == "deadline_exceeded"


def test_late_valid_initial_receipt_can_resolve_manual_without_reopening_apply(executable):
    engine, _, _, refs = executable
    value = unknown(engine, refs)
    assert claim(engine, refs, 900)["operation"]["state"] == "needs_reconciliation"
    with Session(engine) as db:
        final = ExecutionService(db, clock=lambda: NOW + timedelta(seconds=901)).finish(refs.operation_id, receipt=receipt_for(value))
    assert final["state"] == "applied_unverified" and final["recovery"]["attempts"] == 0
    assert final["record"] == value["record"]


def test_late_initial_receipt_wins_and_late_unknown_cannot_overwrite_success(executable):
    engine, _, _, refs = executable
    value = unknown(engine, refs)
    leased = claim(engine, refs)
    with Session(engine) as db:
        service = ExecutionService(db, clock=lambda: NOW + timedelta(seconds=1))
        final = service.finish(refs.operation_id, receipt=receipt_for(value))
        assert service.finish(refs.operation_id, receipt=None) == final
    assert final["state"] == "applied_unverified"
    with pytest.raises(ApprovalError, match="recovery_lease_conflict"):
        report(engine, refs, leased["lease_id"], 2)
    assert claim(engine, refs, 1000)["operation"] == final


def test_receipt_binding_failure_spends_budget_without_deploying_task(executable):
    engine, _, _, refs = executable
    value = unknown(engine, refs)
    token = claim(engine, refs)["lease_id"]
    bad = receipt_for(value)
    bad["request_hash"] = "0" * 64
    result = report(engine, refs, token, error=None, receipt=bad)
    assert result["state"] == "outcome_unknown" and result["recovery"]["last_error"] == "receipt_invalid"
    with Session(engine) as db:
        assert db.get(Task, UUID(refs.task_id)).status == TaskStatus.IN_PROGRESS


def test_service_recovery_cannot_begin_a_ready_operation(executable):
    engine, _, _, refs = executable
    before = reserve(engine, refs)
    assert claim(engine, refs) == {"operation": before, "lease_id": None}
    with Session(engine) as db:
        assert db.get(Task, UUID(refs.task_id)).status == TaskStatus.APPROVED


def test_readiness_requires_the_recovery_guard_not_just_head_stamp(executable):
    from services.management_service.migrations.schema import require_schema
    engine, _, _, _ = executable
    with engine.begin() as conn:
        conn.execute(text("DROP TRIGGER hitl_execution_recovery_guard ON public.management_changelog"))
        with pytest.raises(RuntimeError, match="MANAGEMENT_SCHEMA_NOT_READY"):
            require_schema(conn)


@pytest.mark.parametrize("mutation", ["truncate", "token", "completed", "deadline"])
def test_database_does_not_allow_resetting_or_rewriting_attempts(executable, mutation):
    engine, _, _, refs = executable
    unknown(engine, refs)
    token = claim(engine, refs)["lease_id"]
    report(engine, refs, token)
    with Session(engine) as db:
        row = db.get(Changelog, UUID(refs.operation_id))
        metadata = deepcopy(row.meta)
        if mutation == "truncate":
            metadata["recovery"]["attempts"] = []
        elif mutation == "token":
            metadata["recovery"]["attempts"][0]["lease_id"] = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        elif mutation == "completed":
            metadata["recovery"]["attempts"][0]["error_code"] = "receipt_invalid"
        else:
            metadata["recovery"]["deadline_at"] = "2026-11-09T12:15:01Z"
        row.meta = metadata
        with pytest.raises(DBAPIError, match="HITL_RECOVERY"):
            db.commit()
        db.rollback()


def revision(engine, direction, target):
    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[2] / "services/management_service/migrations").replace("%", "%%"))
    with engine.begin() as conn:
        cfg.attributes.update(connection=conn, management_runner_verified=True)
        getattr(command, direction)(cfg, target)


def seed_legacy_ready(engine, refs):
    """Seed the actual005 wire format on005, without disabling any DB guard."""
    from services.management_service.execution_service import ENTITY, SOURCE, record_hash
    from shared.hitl_execution import bind_proposal_hash, make_command
    from tests.integration.test_management_execution_postgres import BEFORE, BINDING
    revision(engine, "downgrade", "20261114_005_recovery")
    with Session(engine) as db:
        service = ExecutionService(db, clock=lambda: NOW)
        task, _, version, scope, decision, _ = service._authorize(refs, UserActor(ALICE))
        command_body, after = make_command(version.model_dump(mode="json"), refs, BINDING, BEFORE)
        command_body.update(target_url=version.target.url, authorization_deadline=int((NOW + timedelta(seconds=60)).timestamp()))
        bind_proposal_hash(command_body)
        record = {**service._intent(refs, UserActor(ALICE), version, scope, decision), "binding": BINDING.model_dump(),
            "before": BEFORE, "after": after, "command": command_body, "request_hash": digest(command_body)}
        row = Changelog(id=UUID(refs.operation_id), task_id=task.id, project_id=task.project_id, entity_id=refs.operation_id,
            entity_type=ENTITY, change_type="execute_v1", source=SOURCE, applied=False, before_value=BEFORE, after_value=after,
            meta={"record": record, "record_hash": record_hash(record), "state": "ready"})
        db.add(row)
        db.commit()
        return service.serialize(row)


def legacy_begin(writer, refs):
    """Simulate the prior005 writer, so its downgrade race remains covered."""
    from services.management_service.recovery_policy import RecoveryState
    from shared.hitl_execution import canonical
    value = RecoveryState.initial(NOW, int((NOW + timedelta(seconds=60)).timestamp()))
    writer.execute(text("UPDATE public.management_changelog SET metadata=metadata || CAST(:patch AS jsonb) WHERE id=:id"),
        {"id": refs.operation_id, "patch": canonical({"state": "applying", "began_at": NOW.isoformat(), "recovery": value.dump()})})
    writer.execute(text("UPDATE public.tasks SET status='IN_PROGRESS' WHERE id=:id"), {"id": refs.task_id})
    writer.commit()


def test_upgrade_backfills_only_begun_rows_without_changing_frozen_payload(executable):
    engine, _, _, refs = executable
    original = seed_legacy_ready(engine, refs)
    revision(engine, "downgrade", "20261109_004_client_storage")
    with engine.begin() as conn:
        conn.execute(text("UPDATE public.management_changelog SET metadata=metadata || jsonb_build_object('state','applying','began_at',:began)"), {"began": NOW.isoformat()})
        conn.execute(text("UPDATE public.tasks SET status='IN_PROGRESS'"))
    with engine.connect() as conn:
        conn.execute(text("SET TIME ZONE 'Europe/Moscow'"))
        conn.commit()
        migrate(conn, expected_role=engine.url.username)
    result = claim(engine, refs, 900)
    assert result["operation"]["record"] == original["record"]
    assert result["operation"]["record_hash"] == original["record_hash"]
    assert result["operation"]["state"] == "needs_reconciliation"
    with pytest.raises(RuntimeError, match="HITL_RECOVERY_DOWNGRADE_WOULD_RESET_BUDGET"):
        revision(engine, "downgrade", "20261109_004_client_storage")


def test_downgrade_checks_budget_after_waiting_for_concurrent_begin(executable):
    engine, _, _, refs = executable
    seed_legacy_ready(engine, refs)
    with Session(engine) as writer, ThreadPoolExecutor(max_workers=1) as pool:
        # Take RowExclusive before the queued DDL, so begin needs no lock upgrade.
        writer.execute(text("UPDATE public.management_changelog SET applied=applied WHERE id=:id"), {"id": refs.operation_id})
        pending = pool.submit(revision, engine, "downgrade", "20261109_004_client_storage")
        try:
            for _ in range(100):
                with engine.connect() as observer:
                    waiting = observer.scalar(text("SELECT EXISTS(SELECT 1 FROM pg_locks WHERE relation='public.management_changelog'::regclass AND mode='AccessExclusiveLock' AND NOT granted)"))
                if waiting:
                    break
                time.sleep(.02)
            else:
                raise AssertionError("downgrade did not wait on execution writer")
            legacy_begin(writer, refs)
            with pytest.raises(RuntimeError, match="HITL_RECOVERY_DOWNGRADE_WOULD_RESET_BUDGET"):
                pending.result(timeout=10)
        finally:
            writer.rollback()
    assert claim(engine, refs, 900)["operation"]["state"] == "needs_reconciliation"
