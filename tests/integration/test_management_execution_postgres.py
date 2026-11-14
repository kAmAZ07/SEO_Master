"""AR-4 authority/operation races against actual PostgreSQL, no CMS substitute."""
from concurrent.futures import ThreadPoolExecutor
import asyncio
from copy import deepcopy
from datetime import timedelta
from queue import Queue
from threading import Barrier
import time
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from database.models import DomainAuthorization
from services.management_service.approval_policy import ApprovalError, DecisionRequest, UserActor
from services.management_service.execution_service import ExecutionService
from services.management_service.execution_preparation import ExecutionPreparation
from services.management_service.models import Changelog, HITLApproval, Task, TaskStatus
from services.management_service.proposal_handoff import version_diff
from services.semantic_service.db.models import RecommendationVersionRow
from services.semantic_service.proposals.model import ExternalEntity
from services.semantic_service.proposals.repository import row_from_version
from shared.hitl_execution import ExecutionRequest, WordPressBinding
from tests.fixtures.management_proposal import PROJECT_ID, TASK_ID, management_proposal_fixture
from tests.integration.test_management_approvals_postgres import ALICE, APPROVAL, BOB, NOW, WORKSPACE, decide
from tests.integration.test_management_approvals_postgres import stand as approval_stand
from tests.integration.test_migrations_postgres import ADMIN_URL

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="TEST_PG_ADMIN_URL is not set")
stand = approval_stand
BINDING = WordPressBinding(base_url="https://example.com", site_id=WORKSPACE, key_id="test-key")
BEFORE = {"entity_id": 42, "post_type": "post", "post_status": "publish", "fields": {
    "title": {"state": "value", "value": "Мастерская обуви"},
    "description": {"state": "missing"}, "jsonld": {"state": "missing"}}}


@pytest.fixture()
def executable(stand):
    engine, original, _ = stand
    _, _, version = management_proposal_fixture(entity=ExternalEntity(kind="post", id="42"),
        evidence_override=[item.model_dump(mode="json") for item in original.evidence])
    with engine.begin() as conn:
        conn.execute(text("UPDATE audit_schema.workspace_members SET role='owner' WHERE user_id=:actor"), {"actor": ALICE})
    with Session(engine) as session:
        session.query(RecommendationVersionRow).delete()
        session.add(row_from_version(version))
        approval = session.get(HITLApproval, uuid.UUID(APPROVAL))
        approval.diff_data = version_diff(version)
        session.add(DomainAuthorization(id=str(uuid.uuid4()), project_id=PROJECT_ID, workspace_id=WORKSPACE,
            status="verified", scope_origin="https://example.com", scope_path_prefix="/", challenge_hash="0" * 64,
            subject_user_id=ALICE, issued_at=NOW - timedelta(days=1), expires_at=NOW - timedelta(hours=23), verified_at=NOW))
        session.commit()
    decision = DecisionRequest(approval_id=APPROVAL, recommendation_version_id=version.version_id, content_hash=version.content_hash)
    decide(engine, decision)
    references = ExecutionRequest(operation_id=str(uuid.uuid4()), task_id=TASK_ID, approval_id=APPROVAL,
        recommendation_version_id=version.version_id, content_hash=version.content_hash)
    return engine, version, decision, references


def reserve(engine, references, *, now=NOW, before=BEFORE, user=ALICE):
    with Session(engine) as session:
        service = ExecutionService(session, clock=lambda: now)
        if session.query(Changelog).filter_by(id=references.operation_id).first() is None:
            service.queue(references, UserActor(user))
            token = ExecutionPreparation(session, clock=lambda: now).claim(references.operation_id, UserActor(user))["lease_id"]
        else:
            token = service._row(references.operation_id).meta["preparation"]["lease_id"]
        result = service.reserve(references, UserActor(user), BINDING, before, preparation_lease=token)
        return ExecutionPreparation(session, clock=lambda: now).ready(references.operation_id, token) if result["state"] == "dry_run" else result


def begin(engine, references, *, now=NOW):
    with Session(engine) as session:
        service = ExecutionService(session, clock=lambda: now)
        token = service._row(references.operation_id).meta["preparation"]["lease_id"]
        return service.begin(references.operation_id, UserActor(ALICE), preparation_lease=token)


def test_reserve_is_separate_from_apply_and_retry_preserves_exact_command(executable):
    engine, version, _, refs = executable
    first = reserve(engine, refs)
    retry = reserve(engine, refs, now=NOW + timedelta(seconds=20))
    assert first == retry
    assert first["state"] == "ready"
    assert first["record"]["version"] == version.model_dump(mode="json")
    assert first["record"]["command"]["target_url"] == version.target.url
    assert first["record"]["actor_id"] == ALICE and first["record"]["actor_role"] == "owner"
    with Session(engine) as session:
        assert session.get(Task, uuid.UUID(TASK_ID)).status == TaskStatus.APPROVED
    assert begin(engine, refs)["state"] == "applying"


@pytest.mark.parametrize("mutation", ["expired", "revoked", "role", "domain", "actor", "before", "hash"])
def test_invalid_authority_cannot_reserve_operation(executable, mutation):
    engine, _, decision, refs = executable
    now, before, actor = NOW, deepcopy(BEFORE), ALICE
    if mutation == "expired":
        now += timedelta(hours=24)
    elif mutation == "revoked":
        decide(engine, decision, action="revoke")
    elif mutation in {"role", "domain"}:
        with engine.begin() as conn:
            conn.execute(text("UPDATE audit_schema.workspace_members SET role='viewer' WHERE user_id=:actor") if mutation == "role"
                         else text("UPDATE audit_schema.domain_authorizations SET verified_at=:old"),
                         {"actor": ALICE, "old": NOW - timedelta(days=30)})
    elif mutation == "actor":
        actor = BOB
    elif mutation == "before":
        before["fields"]["title"]["value"] = "manual drift"
    else:
        refs = refs.model_copy(update={"content_hash": "0" * 64})
    with pytest.raises((ApprovalError, ValueError)):
        reserve(engine, refs, now=now, before=before, user=actor)
    with engine.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM public.management_changelog")) == (1 if mutation == "before" else 0)
        if mutation == "before":
            assert conn.scalar(text("SELECT metadata->'prepared' FROM public.management_changelog")) is False


def test_begin_rechecks_deadline_and_current_role_after_reservation(executable):
    engine, _, _, refs = executable
    reserve(engine, refs)
    with pytest.raises(ApprovalError, match="execution_expired"):
        begin(engine, refs, now=NOW + timedelta(seconds=60))
    with engine.begin() as conn:
        conn.execute(text("UPDATE audit_schema.workspace_members SET role='reviewer' WHERE user_id=:actor"), {"actor": ALICE})
    with pytest.raises(ApprovalError):
        begin(engine, refs)


def test_competing_begin_has_one_winner_and_inflight_decision_is_frozen(executable):
    engine, _, decision, refs = executable
    reserve(engine, refs)
    barrier = Barrier(2)
    def attempt(_):
        barrier.wait(timeout=5)
        try:
            return begin(engine, refs)["state"]
        except ApprovalError:
            return "conflict"
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt, range(2))) == ["applying", "conflict"]
    with pytest.raises(ApprovalError):
        decide(engine, decision, action="revoke")
    with Session(engine) as session:
        ExecutionService(session, clock=lambda: NOW).finish(refs.operation_id, receipt=None)
    with pytest.raises(ApprovalError):
        begin(engine, refs)
    with engine.connect() as conn:
        assert conn.scalar(text("SELECT metadata->>'state' FROM public.management_changelog")) == "outcome_unknown"


def test_database_prevents_replacing_frozen_command_and_deleting_history(executable):
    engine, _, _, refs = executable
    reserve(engine, refs)
    for statement in ["UPDATE public.management_changelog SET metadata=jsonb_set(metadata,'{record,actor_id}','\"forged\"')",
                      "DELETE FROM public.management_changelog"]:
        with pytest.raises(DBAPIError, match="HITL_EXECUTION_IMMUTABLE"):
            with engine.begin() as conn:
                conn.execute(text(statement))


def test_late_saga_failure_cannot_reopen_inflight_task_for_another_version(executable):
    from services.management_service.approval_service import ApprovalService
    from services.management_service.orchestrator import OptimizationSaga
    engine, version, _, refs = executable
    reserve(engine, refs)
    begin(engine, refs)
    with Session(engine) as session:
        saga = OptimizationSaga(PROJECT_ID, version.target.url, TASK_ID)
        asyncio.run(saga._update_task_status(session, TaskStatus.FAILED))
        assert session.get(Task, uuid.UUID(TASK_ID)).status == TaskStatus.IN_PROGRESS
        newer = version.model_copy(update={"version_id": str(uuid.uuid4()), "version": 2, "previous_version_id": version.version_id})
        session.add(row_from_version(newer))
        session.commit()
        with pytest.raises(ApprovalError, match="approval_task_state_conflict"):
            ApprovalService(session, clock=lambda: NOW).prepare(newer, metadata={})
    with engine.connect() as conn:
        assert conn.scalar(text("SELECT metadata->>'state' FROM public.management_changelog")) == "applying"


def test_head_without_unique_execution_index_is_not_ready(executable):
    from services.management_service.migrations.schema import require_schema
    engine, _, _, _ = executable
    with engine.begin() as conn:
        conn.execute(text("DROP INDEX public.uq_hitl_execution_version"))
        with pytest.raises(RuntimeError, match="MANAGEMENT_SCHEMA_NOT_READY"):
            require_schema(conn)


def test_durable_inbox_commits_before_ack_and_redelivery_cannot_decide_or_apply(executable):
    from unittest.mock import AsyncMock
    import json
    from services.event_resilience import ResilientConsumerConfig
    from services.management_service.events.hitl_inbox import CONSUMER, process_notification, record_notification
    engine, _, _, _ = executable
    with engine.begin() as conn:
        event = conn.scalar(text("SELECT metadata FROM public.management_outbox WHERE event_type='HITLApproved'"))
    async def check_ack():
        with engine.connect() as conn:
            assert conn.scalar(text("SELECT count(*) FROM public.processed_events")) == 1
    config = ResilientConsumerConfig(consumer_name=CONSUMER, queue_name=CONSUMER, routing_key="hitl.approved")
    message = AsyncMock()
    message.body = json.dumps(event).encode()
    message.headers = {}
    message.ack.side_effect = check_ack
    async def deliver():
        for _ in range(2):
            await process_notification(message, config=config, factory=lambda: Session(engine), retry_exchange=AsyncMock())
    asyncio.run(deliver())
    assert message.ack.call_count == 2
    message.reject.assert_not_called()
    forged = deepcopy(event)
    forged["payload"]["actor_id"] = BOB
    with Session(engine) as session, pytest.raises(ApprovalError):
        record_notification(session, forged)
    with engine.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM public.management_changelog")) == 0
        assert conn.scalar(text("SELECT count(*) FROM public.processed_events")) == 1


def test_repeatable_read_authority_is_rejected_before_using_a_stale_snapshot(executable):
    engine, _, _, refs = executable
    with Session(engine.execution_options(isolation_level="REPEATABLE READ")) as session:
        with pytest.raises(ApprovalError, match="authority_isolation_unsupported"):
            ExecutionService(session, clock=lambda: NOW).context(refs, UserActor(ALICE))


@pytest.mark.parametrize("mutation", ["role", "version"])
def test_begin_waiting_for_producer_commit_reads_fresh_authority(executable, mutation):
    engine, version, _, refs = executable
    reserve(engine, refs)
    waiting_pid = Queue()
    def attempt():
        with Session(engine) as session:
            waiting_pid.put(session.scalar(text("SELECT pg_backend_pid()")))
            token = ExecutionService(session)._row(refs.operation_id).meta["preparation"]["lease_id"]
            with pytest.raises(ApprovalError):
                ExecutionService(session, clock=lambda: NOW).begin(refs.operation_id, UserActor(ALICE), preparation_lease=token)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with Session(engine) as producer:
            if mutation == "role":
                producer.execute(text("UPDATE audit_schema.workspace_members SET role='viewer' WHERE user_id=:id"), {"id": ALICE})
            else:
                newer = version.model_copy(update={"version_id": str(uuid.uuid4()), "version": 2, "previous_version_id": version.version_id})
                producer.add(row_from_version(newer))
                producer.flush()
            future = pool.submit(attempt)
            pid = waiting_pid.get(timeout=5)
            # Prove that begin actually waits on the producer transaction, not a sleep.
            deadline = time.monotonic() + 4
            with engine.connect() as observer:
                while not observer.scalar(text("SELECT EXISTS(SELECT 1 FROM pg_locks WHERE pid=:pid AND locktype='advisory' AND NOT granted)"), {"pid": pid}):
                    assert not future.done() and time.monotonic() < deadline
                    time.sleep(.01)
            producer.commit()
        future.result(timeout=5)
    with engine.connect() as conn:
        assert conn.scalar(text("SELECT metadata->>'state' FROM public.management_changelog")) == "ready"
        assert conn.scalar(text("SELECT status::text FROM public.tasks WHERE id=:id"), {"id": TASK_ID}) == TaskStatus.APPROVED.name
