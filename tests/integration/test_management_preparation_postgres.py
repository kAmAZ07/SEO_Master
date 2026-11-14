"""AR-6 intent, one-time freeze and pre-write phases on actual PostgreSQL."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from queue import Queue
import time
from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from services.management_service.approval_policy import ApprovalError, UserActor
from services.management_service.execution_preparation import ExecutionPreparation
from services.management_service.execution_service import ExecutionService
from services.management_service.models import Changelog, Task, TaskStatus
from shared.hitl_execution import PreparationFailure
from tests.integration.test_management_execution_postgres import executable as execution_fixture, stand as approval_fixture, BEFORE, BINDING
from tests.integration.test_management_approvals_postgres import ALICE, NOW
from tests.integration.test_migrations_postgres import ADMIN_URL

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="TEST_PG_ADMIN_URL is not set")
executable = execution_fixture
stand = approval_fixture


def queue(engine, refs, seconds=0):
    with Session(engine) as db:
        return ExecutionService(db, clock=lambda: NOW + timedelta(seconds=seconds)).queue(refs, UserActor(ALICE))


def claim(engine, refs, seconds=0):
    with Session(engine) as db:
        return ExecutionPreparation(db, clock=lambda: NOW + timedelta(seconds=seconds)).claim(refs.operation_id, UserActor(ALICE))


def freeze(engine, refs, token, seconds=0, before=BEFORE):
    with Session(engine) as db:
        return ExecutionService(db, clock=lambda: NOW + timedelta(seconds=seconds)).reserve(refs, UserActor(ALICE), BINDING, before,
            preparation_lease=token)


def ready(engine, refs, token, seconds=0):
    with Session(engine) as db:
        return ExecutionPreparation(db, clock=lambda: NOW + timedelta(seconds=seconds)).ready(refs.operation_id, token)


def test_queue_persists_immutable_intent_with_sql_null_snapshots(executable):
    engine, version, _, refs = executable
    first = queue(engine, refs)
    assert first["state"] == "queued" and first["prepared"] is False
    assert "command" not in first["record"] and "before" not in first["record"]
    assert first["record"]["version"] == version.model_dump(mode="json")
    assert queue(engine, refs) == first
    with engine.connect() as conn:
        assert conn.scalar(text("SELECT before_value IS NULL AND after_value IS NULL FROM public.management_changelog")) is True
    with Session(engine) as db:
        assert db.get(Task, UUID(refs.task_id)).status == TaskStatus.APPROVED
    with pytest.raises(DBAPIError, match="HITL_EXECUTION_IMMUTABLE"):
        with engine.begin() as conn:
            conn.execute(text("UPDATE public.management_changelog SET metadata=jsonb_set(metadata,'{intent,actor_id}','\"forged\"')"))


def test_single_freeze_dry_run_ready_begin_and_stale_failure_cannot_downgrade(executable):
    engine, _, _, refs = executable
    queued = queue(engine, refs)
    token = claim(engine, refs)["lease_id"]
    frozen = freeze(engine, refs, token)
    assert frozen["state"] == "dry_run" and frozen["prepared"] is True
    assert frozen["record"]["before"] == BEFORE
    assert frozen["record_hash"] != queued["record_hash"]
    final_ready = ready(engine, refs, token)
    assert [event["phase"] for event in final_ready["preparation"]["events"]] == ["queued", "preflight", "dry_run", "ready"]
    with Session(engine) as db:
        service = ExecutionService(db, clock=lambda: NOW)
        begun = service.begin(refs.operation_id, UserActor(ALICE), preparation_lease=token)
        assert begun["state"] == "applying" and begun["record"] == frozen["record"]
        with pytest.raises(ApprovalError, match="preparation_lease_conflict"):
            ExecutionPreparation(db, clock=lambda: NOW).fail(refs.operation_id,
                PreparationFailure(lease_id=token, error_code="cms_unavailable"))
        assert service._row(refs.operation_id).meta["state"] == "applying"


def test_busy_or_expired_lease_never_resets_command_or_authorization_deadline(executable):
    engine, _, _, refs = executable
    queue(engine, refs)
    token = claim(engine, refs)["lease_id"]
    with pytest.raises(ApprovalError, match="execution_preparation_busy"):
        claim(engine, refs, 1)
    first = freeze(engine, refs, token)
    with pytest.raises(ApprovalError, match="preparation_lease_conflict"):
        freeze(engine, refs, token, 60)
    result = claim(engine, refs, 60)
    assert result["lease_id"] is None and result["operation"]["state"] == "failed_before_apply"
    assert result["operation"]["record"] == first["record"]


def test_readonly_restart_before_freeze_uses_same_intent_and_spends_next_lease(executable):
    engine, _, _, refs = executable
    original = queue(engine, refs)
    first = claim(engine, refs)["lease_id"]
    second = claim(engine, refs, 60)
    assert second["lease_id"] != first and second["operation"]["preparation"]["attempts"] == 2
    assert second["operation"]["record"] == original["record"]
    with pytest.raises(ApprovalError, match="preparation_lease_conflict"):
        freeze(engine, refs, first, 60)
    assert freeze(engine, refs, second["lease_id"], 60)["state"] == "dry_run"


def test_repeated_prewrite_crashes_have_a_persisted_budget_and_do_not_abort_a_live_final_lease(executable):
    engine, _, _, refs = executable
    original = queue(engine, refs)
    for attempt, seconds in enumerate((0, 60, 120, 180), 1):
        result = claim(engine, refs, seconds)
        assert result["operation"]["record"] == original["record"]
        assert result["operation"]["preparation"]["attempts"] == attempt
    with pytest.raises(ApprovalError, match="execution_preparation_busy"):
        claim(engine, refs, 181)
    failed = claim(engine, refs, 240)
    assert failed["lease_id"] is None and failed["operation"]["state"] == "failed_before_apply"
    assert failed["operation"]["preparation"]["attempts"] == 4
    assert failed["operation"]["preparation"]["error_code"] == "preparation_expired"
    assert "command" not in failed["operation"]["record"]


@pytest.mark.parametrize("error,state", [("cms_unavailable", "failed_before_apply"), ("authority_unavailable", "failed_before_apply"),
    ("authorization_rejected", "rejected"), ("cms_precondition_conflict", "conflict"), ("cms_unsupported", "unsupported")])
def test_prewrite_failure_records_reason_and_leaves_task_approved(executable, error, state):
    engine, _, _, refs = executable
    queue(engine, refs)
    token = claim(engine, refs)["lease_id"]
    with Session(engine) as db:
        result = ExecutionPreparation(db, clock=lambda: NOW).fail(refs.operation_id, PreparationFailure(lease_id=token, error_code=error))
        assert result["state"] == state and result["preparation"]["error_code"] == error
        assert db.get(Task, UUID(refs.task_id)).status == TaskStatus.APPROVED
    assert claim(engine, refs)["lease_id"] is None


def test_competing_queue_ids_for_same_version_have_one_winner(executable):
    from uuid import uuid4
    engine, _, _, refs = executable
    barrier = Barrier(2)
    def attempt(item):
        barrier.wait(timeout=5)
        try:
            return queue(engine, item)["operation_id"]
        except ApprovalError as exc:
            assert exc.code == "execution_already_exists"
            return "conflict"
    with ThreadPoolExecutor(max_workers=2) as pool:
        values = list(pool.map(attempt, [refs, refs.model_copy(update={"operation_id": str(uuid4())})]))
    assert values.count("conflict") == 1
    with engine.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM public.management_changelog")) == 1


def test_same_queue_id_is_idempotent_under_concurrency(executable):
    engine, _, _, refs = executable
    barrier = Barrier(2)
    def attempt(_):
        barrier.wait(timeout=5)
        return queue(engine, refs)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.map(attempt, range(2))
    assert first == second and first["state"] == "queued"


def test_cancel_and_begin_have_one_winner_and_old_callbacks_cannot_resume(executable):
    engine, _, _, refs = executable
    queue(engine, refs)
    token = claim(engine, refs)["lease_id"]
    freeze(engine, refs, token)
    ready(engine, refs, token)
    barrier = Barrier(2)
    def attempt(action):
        barrier.wait(timeout=5)
        with Session(engine) as db:
            try:
                if action == "cancel":
                    return ExecutionPreparation(db, clock=lambda: NOW).cancel(refs.operation_id, UserActor(ALICE))["state"]
                return ExecutionService(db, clock=lambda: NOW).begin(refs.operation_id, UserActor(ALICE), preparation_lease=token)["state"]
            except ApprovalError:
                return "conflict"
    with ThreadPoolExecutor(max_workers=2) as pool:
        values = list(pool.map(attempt, ["cancel", "begin"]))
    assert values.count("conflict") == 1
    with Session(engine) as db:
        row = db.get(Changelog, UUID(refs.operation_id))
        expected = "cancelled" if "cancelled" in values else "applying"
        assert row.meta["state"] == expected
        before = dict(row.meta)
        with pytest.raises(ApprovalError):
            ExecutionPreparation(db, clock=lambda: NOW).fail(refs.operation_id, PreparationFailure(lease_id=token, error_code="cms_unavailable"))
        assert db.get(Changelog, UUID(refs.operation_id)).meta == before
        assert db.get(Task, UUID(refs.task_id)).status == (TaskStatus.APPROVED if expected == "cancelled" else TaskStatus.IN_PROGRESS)


@pytest.mark.parametrize("action", ["freeze", "ready", "failure"])
def test_callback_waiting_on_task_lock_uses_fresh_lease_time(executable, action):
    engine, _, _, refs = executable
    queue(engine, refs)
    token = claim(engine, refs)["lease_id"]
    if action == "ready":
        freeze(engine, refs, token)
    clock = [NOW]
    pid_queue = Queue()
    def callback():
        with Session(engine) as db:
            pid_queue.put(db.scalar(text("SELECT pg_backend_pid()")))
            with pytest.raises(ApprovalError, match="preparation_lease_conflict"):
                if action == "freeze":
                    ExecutionService(db, clock=lambda: clock[0]).reserve(refs, UserActor(ALICE), BINDING, BEFORE, preparation_lease=token)
                elif action == "ready":
                    ExecutionPreparation(db, clock=lambda: clock[0]).ready(refs.operation_id, token)
                else:
                    ExecutionPreparation(db, clock=lambda: clock[0]).fail(refs.operation_id, PreparationFailure(lease_id=token, error_code="cms_unavailable"))
    with Session(engine) as holder, ThreadPoolExecutor(max_workers=1) as pool:
        holder.execute(text("SELECT id FROM public.tasks WHERE id=:id FOR UPDATE"), {"id": refs.task_id})
        pending = pool.submit(callback)
        pid = pid_queue.get(timeout=5)
        try:
            for _ in range(100):
                with engine.connect() as observer:
                    if observer.scalar(text("SELECT wait_event_type='Lock' FROM pg_stat_activity WHERE pid=:pid"), {"pid": pid}):
                        break
                time.sleep(.02)
            else:
                raise AssertionError("callback did not wait for Task lock")
            clock[0] = NOW + timedelta(seconds=61)
        finally:
            holder.commit()
        pending.result(timeout=10)
    with Session(engine) as db:
        row = db.get(Changelog, UUID(refs.operation_id))
        assert row.meta["state"] == ("dry_run" if action == "ready" else "preflight")
        assert db.get(Task, UUID(refs.task_id)).status == TaskStatus.APPROVED


def test_old_preflight_failure_does_not_modify_a_newer_approval(executable):
    from uuid import uuid4
    from services.management_service.approval_service import ApprovalService
    from services.semantic_service.proposals.repository import row_from_version
    engine, version, _, refs = executable
    queue(engine, refs)
    token = claim(engine, refs)["lease_id"]
    newer = version.model_copy(update={"version_id": str(uuid4()), "version": 2, "previous_version_id": version.version_id})
    with Session(engine) as db:
        db.add(row_from_version(newer))
        db.commit()
        ApprovalService(db, clock=lambda: NOW).prepare(newer, metadata={})
        assert db.get(Task, UUID(refs.task_id)).status == TaskStatus.PENDING
    with pytest.raises(ApprovalError):
        freeze(engine, refs, token)
    with Session(engine) as db:
        result = ExecutionPreparation(db, clock=lambda: NOW).fail(refs.operation_id, PreparationFailure(lease_id=token, error_code="authorization_rejected"))
        assert result["state"] == "rejected"
        assert db.get(Task, UUID(refs.task_id)).status == TaskStatus.PENDING


def test_budget_and_frozen_prefix_cannot_be_rewritten_in_database(executable):
    engine, _, _, refs = executable
    queue(engine, refs)
    token = claim(engine, refs)["lease_id"]
    frozen = freeze(engine, refs, token)
    for statement in [
        "UPDATE public.management_changelog SET metadata=jsonb_set(metadata,'{record,command,authorization_deadline}','9999999999')",
        "UPDATE public.management_changelog SET metadata=jsonb_set(metadata,'{prepared}','false')",
        "UPDATE public.management_changelog SET metadata=jsonb_set(metadata,'{preparation,attempts}','0')",
        "UPDATE public.management_changelog SET metadata=jsonb_set(metadata,'{preparation,events,0,phase}','\"ready\"')",
    ]:
        with pytest.raises(DBAPIError, match="HITL_"):
            with engine.begin() as conn:
                conn.execute(text(statement))
    with Session(engine) as db:
        assert ExecutionService.serialize(db.get(Changelog, UUID(refs.operation_id)))["record"] == frozen["record"]


@pytest.mark.parametrize("state", ["ready", "applying", "applied_unverified"])
def test_upgrade005_preserves_legacy_operation_and_recovery_bytes(executable, state):
    from services.management_service.execution_recovery import ExecutionRecovery
    from shared.hitl_execution import RecoveryReport
    from tests.integration.test_management_recovery_postgres import seed_legacy_ready, legacy_begin, receipt_for, revision
    engine, _, _, refs = executable
    original = seed_legacy_ready(engine, refs)
    if state != "ready":
        with Session(engine) as db:
            legacy_begin(db, refs)
            if state == "applied_unverified":
                ExecutionService(db, clock=lambda: NOW).finish(refs.operation_id, receipt=receipt_for(original))
            else:
                ExecutionService(db, clock=lambda: NOW).finish(refs.operation_id, receipt=None)
                token = ExecutionRecovery(db, clock=lambda: NOW).claim(refs.operation_id)["lease_id"]
                ExecutionRecovery(db, clock=lambda: NOW).report(refs.operation_id, RecoveryReport(lease_id=token, error_code="cms_unavailable"))
    with engine.connect() as conn:
        before = conn.execute(text("SELECT metadata,before_value,after_value,created_at,applied FROM public.management_changelog")).one()
    revision(engine, "upgrade", "head")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT metadata,before_value,after_value,created_at,applied FROM public.management_changelog")).one() == before


def test_downgrade_waits_for_queued_intent_and_refuses_to_lose_it(executable):
    from tests.integration.test_management_recovery_postgres import revision
    engine, _, _, refs = executable
    with Session(engine) as writer, ThreadPoolExecutor(max_workers=1) as pool:
        writer.execute(text("LOCK TABLE public.management_changelog IN ROW EXCLUSIVE MODE"))
        pending = pool.submit(revision, engine, "downgrade", "20261114_005_recovery")
        try:
            for _ in range(100):
                with engine.connect() as observer:
                    if observer.scalar(text("SELECT EXISTS(SELECT 1 FROM pg_locks WHERE relation='public.management_changelog'::regclass AND mode='AccessExclusiveLock' AND NOT granted)")):
                        break
                time.sleep(.02)
            else:
                raise AssertionError("downgrade did not wait for writer")
            ExecutionService(writer, clock=lambda: NOW).queue(refs, UserActor(ALICE))
            with pytest.raises(RuntimeError, match="HITL_PREPARATION_DOWNGRADE_WOULD_LOSE_HISTORY"):
                pending.result(timeout=10)
        finally:
            writer.rollback()
