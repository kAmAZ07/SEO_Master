"""PostgreSQL ledger migration, regeneration and lease recovery; transport faults are injected."""

import asyncio
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
import runpy
from threading import Barrier
import uuid

from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
from sqlalchemy import event, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from database.models import Project as CanonicalProject, Workspace
from tests.integration.test_migrations_postgres import TempDatabase
from tests.integration.test_management_migrations_postgres import legacy
from services.management_service.migrations.runner import migrate
from services.management_service.migrations.schema import LEDGER_HEAD

from tests.integration.test_management_approvals_postgres import (
    ADMIN_URL, ALICE, BOB, APPROVAL, NOW, TASK_ID, decide, stand as approval_stand, state,
)
from services.management_service.approval_policy import ApprovalError, DecisionRequest, UserActor
from services.management_service.approval_service import ApprovalService
from services.management_service.approval_ledger import snapshot_hash
from services.management_service.models import HITLApproval, HITLHistory, ManagementOutbox, Task, TaskStatus
from services.management_service.outbox import claim_event, drain_outbox, finish_event
from services.semantic_service.proposals.repository import row_from_version

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="TEST_PG_ADMIN_URL is not set")
stand = approval_stand


def factory(engine):
    return lambda: Session(engine)


def revision():
    return runpy.run_path(str(Path(__file__).resolve().parents[2] /
                             "services/management_service/migrations/versions/20261109_001_hitl_ledger.py"))


def test_regeneration_keeps_original_decision_and_requires_new_explicit_approval(stand):
    engine, version, command = stand
    first = decide(engine, command)
    newer = version.model_copy(update={"version_id": str(uuid.uuid4()), "version": 2,
                                       "previous_version_id": version.version_id})
    with Session(engine) as db:
        db.add(row_from_version(newer))
        db.commit()
        prepared = ApprovalService(db, clock=lambda: NOW).prepare(newer, metadata={})
        assert str(prepared.id) == APPROVAL
        assert prepared.approved_by is None and prepared.approved_at is None
        assert "decision_record" not in prepared.meta
        assert state(engine) == "pending"
        ApprovalService(db, clock=lambda: NOW).prepare(newer, metadata={})
    with pytest.raises(ApprovalError):
        decide(engine, command)
    next_command = DecisionRequest.model_validate({**command.model_dump(), "recommendation_version_id": newer.version_id})
    second = decide(engine, next_command)
    assert second["approval_hash"] != first["approval_hash"]
    with Session(engine) as db:
        history = ApprovalService(db).history(TASK_ID, UserActor(ALICE))
        assert history == (ApprovalService(db).history(TASK_ID, UserActor(ALICE), limit=2)
                           + ApprovalService(db).history(TASK_ID, UserActor(ALICE), limit=2, offset=2))
        approvals = [entry for entry in history if entry["entry_type"] == "approve"]
        assert len(approvals) == 2
        old = next(entry for entry in approvals if entry["version_id"] == version.version_id)
        assert old["snapshot"]["decision_record"] == first["decision_record"]
        assert len(db.query(ManagementOutbox).all()) == 3  # approve v1, prepare v2, approve v2


def test_outbox_insert_failure_rolls_back_the_decision_and_history(stand):
    engine, _, command = stand

    def fail_outbox(_conn, _cursor, statement, _params, _context, _many):
        if statement.startswith("INSERT INTO management_outbox"):
            raise RuntimeError("synthetic outbox insert failure")

    event.listen(engine, "before_cursor_execute", fail_outbox)
    try:
        with pytest.raises(RuntimeError, match="outbox insert failure"):
            decide(engine, command)
    finally:
        event.remove(engine, "before_cursor_execute", fail_outbox)
    assert state(engine) == "pending"
    with Session(engine) as db:
        assert db.query(HITLHistory).count() == db.query(ManagementOutbox).count() == 0


def test_real_database_prevents_history_rewrite_delete_and_event_payload_changes(stand):
    engine, _, command = stand
    decide(engine, command)
    for sql, code in [
        ("UPDATE public.hitl_history SET metadata = '{}'", "HITL_HISTORY_IMMUTABLE"),
        ("DELETE FROM public.hitl_history", "HITL_HISTORY_IMMUTABLE"),
        ("UPDATE public.management_outbox SET metadata = '{}'", "OUTBOX_ENVELOPE_IMMUTABLE"),
        ("UPDATE public.management_outbox SET routing_key = 'other'", "OUTBOX_ENVELOPE_IMMUTABLE"),
    ]:
        with pytest.raises(DBAPIError, match=code):
            with engine.begin() as conn:
                conn.execute(text(sql))


def test_downgrade_refuses_to_delete_existing_decisions_or_events(stand):
    engine, _, command = stand
    decide(engine, command)
    with pytest.raises(RuntimeError, match="AR4_LEDGER_DOWNGRADE_WOULD_LOSE_DATA"):
        with engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
            revision()["downgrade"]()
    with Session(engine) as db:
        assert db.query(HITLHistory).count() == db.query(ManagementOutbox).count() == 1


def test_history_is_project_scoped_even_when_foreign_user_knows_task_id(stand):
    engine, _, command = stand
    decide(engine, command)
    with Session(engine) as db:
        with pytest.raises(ApprovalError) as error:
            ApprovalService(db).history(TASK_ID, UserActor(BOB))
        assert error.value.http_status == 404


def test_history_does_not_expose_foreign_project_tagged_rows(stand):
    engine, version, command = stand
    decide(engine, command)
    foreign = {"private": "foreign imported data"}
    with Session(engine) as db:
        db.add(Workspace(id=BOB, owner_id=BOB, name="Foreign workspace", kind="personal"))
        db.flush()
        db.add(CanonicalProject(id=BOB, owner_id=BOB, workspace_id=BOB, name="Foreign project",
                                url="https://example.com/", origin="https://example.com", path_prefix="/", status="active"))
        db.flush()
        db.add(HITLHistory(id=uuid.uuid4(), approval_id=uuid.UUID(APPROVAL), task_id=uuid.UUID(TASK_ID),
                           project_id=uuid.UUID(BOB), version_id=version.version_id, entry_type="prepared",
                           content_hash=version.content_hash, entry_hash=snapshot_hash(foreign), meta=foreign))
        db.commit()
        history = ApprovalService(db).history(TASK_ID, UserActor(ALICE))
        assert len(history) == 1 and history[0]["entry_type"] == "approve"


def test_notification_replay_only_reads_existing_human_decision(stand):
    from services.management_service.hitl_handler import handle_hitl_approved_event
    engine, _, command = stand
    original = decide(engine, command)
    with Session(engine) as db:
        envelope = db.query(ManagementOutbox).one().meta
        for _ in range(2):
            receipt = asyncio.run(handle_hitl_approved_event(db, envelope))
            assert receipt["status"] == "notification_recorded"
            assert receipt["approval_hash"] == original["approval_hash"]
        forged = deepcopy(envelope)
        forged["payload"]["actor_id"] = BOB
        with pytest.raises(ApprovalError, match="approval_notification_invalid"):
            asyncio.run(handle_hitl_approved_event(db, forged))
        assert db.query(HITLHistory).count() == db.query(ManagementOutbox).count() == 1
        assert db.get(Task, uuid.UUID(TASK_ID)).status == TaskStatus.APPROVED


def test_empty_revision_round_trip_preserves_old_task():
    # Exercise the ledger revision at its own head, not beneath a newer FK graph.
    with TempDatabase() as database:
        original = legacy(database)
        with database.engine.connect() as conn:
            migrate(conn, expected_role="postgres", target=LEDGER_HEAD)
        with database.engine.begin() as conn, Operations.context(MigrationContext.configure(conn)):
            revision()["downgrade"]()
            assert conn.scalar(text("SELECT to_regclass('public.hitl_history')")) is None
            assert conn.scalar(text("SELECT count(*) FROM public.tasks")) == 1
            revision()["upgrade"]()
        assert database.rows("SELECT row_to_json(p) FROM public.projects p") == original[0]
        assert database.rows("SELECT row_to_json(t) FROM public.tasks t") == original[1]


def test_two_dispatchers_cannot_hold_the_same_active_lease(stand):
    engine, _, command = stand
    decide(engine, command)
    barrier = Barrier(2)

    def claim(_):
        barrier.wait(timeout=5)
        return claim_event(factory(engine), now=NOW)

    with ThreadPoolExecutor(max_workers=2) as pool:
        leases = list(pool.map(claim, [0, 1]))
    assert sum(lease is not None for lease in leases) == 1


def test_expired_lease_recovers_same_event_and_rejects_stale_ack(stand):
    engine, _, command = stand
    decide(engine, command)
    first = claim_event(factory(engine), now=NOW)
    assert claim_event(factory(engine), now=NOW + timedelta(seconds=59)) is None
    later = NOW + timedelta(seconds=61)
    second = claim_event(factory(engine), now=later)
    assert second.event_id == first.event_id and second.envelope == first.envelope
    assert second.lease_token != first.lease_token and second.attempt == 2
    assert finish_event(factory(engine), first, now=later) is False
    assert finish_event(factory(engine), second, now=later) is True
    assert claim_event(factory(engine), now=later + timedelta(days=1)) is None


def test_retry_after_injected_lost_broker_response_keeps_exact_event_id_and_body(stand):
    engine, _, command = stand
    original = decide(engine, command)
    delivered = []

    async def accept_then_lose_response(event):
        delivered.append((event.event_id, event.envelope))
        raise TimeoutError("synthetic response loss after acceptance")

    first = asyncio.run(drain_outbox(factory(engine), accept_then_lose_response, clock=lambda: NOW, max_events=1))
    assert first["failed"] == 1
    assert claim_event(factory(engine), now=NOW + timedelta(seconds=1)) is None

    async def accept(event):
        delivered.append((event.event_id, event.envelope))

    second = asyncio.run(drain_outbox(factory(engine), accept, clock=lambda: NOW + timedelta(seconds=3), max_events=1))
    assert second["published"] == 1 and delivered[0] == delivered[1]
    with Session(engine) as db:
        approval = db.get(HITLApproval, uuid.UUID(APPROVAL))
        assert approval.meta["decision_hash"] == original["approval_hash"]
        row = db.execute(select(ManagementOutbox.status, ManagementOutbox.attempts)).one()
        assert tuple(row) == ("published", 2)


def test_regeneration_does_not_cancel_an_in_progress_task(stand):
    engine, version, command = stand
    decide(engine, command)
    newer = version.model_copy(update={"version_id": str(uuid.uuid4()), "version": 2,
                                       "previous_version_id": version.version_id})
    with Session(engine) as db:
        db.add(row_from_version(newer))
        db.get(Task, uuid.UUID(TASK_ID)).status = TaskStatus.IN_PROGRESS
        db.commit()
        with pytest.raises(ApprovalError, match="approval_task_state_conflict"):
            ApprovalService(db, clock=lambda: NOW).prepare(newer, metadata={})
    with Session(engine) as db:
        assert db.get(Task, uuid.UUID(TASK_ID)).status == TaskStatus.IN_PROGRESS
        assert db.query(ManagementOutbox).count() == 1
