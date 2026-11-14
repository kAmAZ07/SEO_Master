"""AR-7 authoritative verification/rollback races on migrated PostgreSQL."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from services.management_service.approval_policy import ApprovalError, UserActor
from services.management_service.execution_service import ExecutionService
from services.management_service.execution_verification import ExecutionVerification
from services.management_service.execution_preparation import ExecutionPreparation
from services.management_service.models import Task, TaskStatus
from shared.hitl_execution import ExecutionRequest
from shared.hitl_rollback import RollbackRequest
from shared.hitl_verification import PublicFacts, PublicObservation, VerificationReport, VerificationResult
from tests.integration.test_management_execution_postgres import ALICE, BEFORE, BINDING, NOW, TASK_ID, begin, reserve
from tests.integration.test_management_execution_postgres import executable as execution_fixture
from tests.integration.test_management_execution_postgres import stand as approval_stand
from tests.integration.test_management_recovery_postgres import receipt_for
from tests.integration.test_migrations_postgres import ADMIN_URL

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="TEST_PG_ADMIN_URL is not set")
executable = execution_fixture
stand = approval_stand


def confirmed(engine, refs):
    reserve(engine, refs)
    begun = begin(engine, refs)
    with Session(engine) as session:
        return ExecutionService(session, clock=lambda: NOW).finish(refs.operation_id, receipt=receipt_for(begun))


def observation(operation, *, seconds=1, title=None):
    record = operation["record"]
    title = record["after"]["fields"]["title"]["value"] if title is None else title
    return PublicObservation(url=record["version"]["target"]["url"], final_url=record["version"]["target"]["url"],
        observed_at=NOW+timedelta(seconds=seconds), status=200, headers={"content-type": "text/html", "age": "0"},
        body_sha256="b"*64, facts=PublicFacts(titles=(title,), descriptions=(), jsonld=(), h1=(), links=()))


def claim(engine, refs, seconds=0):
    with Session(engine) as session:
        return ExecutionVerification(session, clock=lambda: NOW+timedelta(seconds=seconds)).claim(refs.operation_id)


def report(engine, refs, token, operation, *, seconds=1, title=None):
    with Session(engine) as session:
        return ExecutionVerification(session, clock=lambda: NOW+timedelta(seconds=seconds)).report(refs.operation_id,
            VerificationReport(lease_id=token, result=VerificationResult(observation=observation(operation, seconds=seconds, title=title))))


def test_only_public_match_completes_task_and_late_finish_cannot_reset_it(executable):
    engine, _, _, refs = executable
    operation = confirmed(engine, refs)
    assert operation["state"] == "applied_unverified"
    claimed = claim(engine, refs)
    mismatch = report(engine, refs, claimed["lease_id"], operation, title="Old CDN value")
    assert mismatch["state"] == "verifying"
    with Session(engine) as session:
        assert session.get(Task, UUID(TASK_ID)).status == TaskStatus.DEPLOYED
    token = claim(engine, refs, 6)["lease_id"]
    result = report(engine, refs, token, operation, seconds=7)
    assert result["state"] == "verified" and result["verification"]["attempts"] == 2
    with Session(engine) as session:
        service = ExecutionService(session, clock=lambda: NOW+timedelta(seconds=8))
        assert service.finish(refs.operation_id, receipt=None)["state"] == "verified"
        assert session.get(Task, UUID(TASK_ID)).status == TaskStatus.COMPLETED


def test_concurrent_claim_has_one_durable_public_read_lease(executable):
    engine, _, _, refs = executable
    confirmed(engine, refs)
    barrier = Barrier(2)
    def run(_):
        barrier.wait(timeout=5)
        return claim(engine, refs)
    with ThreadPoolExecutor(max_workers=2) as pool:
        values = list(pool.map(run, range(2)))
    assert sum(value["lease_id"] is not None for value in values) == 1
    assert claim(engine, refs)["operation"]["verification"]["attempts"] == 1


def test_stale_report_does_not_verify_after_restart_or_deadline(executable):
    engine, _, _, refs = executable
    operation = confirmed(engine, refs)
    token = claim(engine, refs)["lease_id"]
    with pytest.raises(ApprovalError, match="verification_lease_conflict"):
        report(engine, refs, token, operation, seconds=30)
    failed = claim(engine, refs, 300)["operation"]
    assert failed["state"] == "verification_failed"
    assert failed["verification"]["stop_reason"] == "deadline_exceeded"
    assert claim(engine, refs, 301)["lease_id"] is None


@pytest.mark.parametrize("mutation", ["deadline", "receipt", "remove_journal"])
def test_database_preserves_verification_deadline_and_receipt(executable, mutation):
    engine, _, _, refs = executable
    confirmed(engine, refs)
    statements = {"deadline": "metadata=jsonb_set(metadata,'{verification,deadline_seconds}','3600')",
        "receipt": "metadata=jsonb_set(metadata,'{receipt,signature}','\"forged\"')",
        "remove_journal": "metadata=metadata-'verification'"}
    with pytest.raises(DBAPIError):
        with engine.begin() as conn:
            conn.execute(text("UPDATE public.management_changelog SET "+statements[mutation]))


def test_rollback_begin_supersedes_public_token_and_reuses_the_original_snapshot(executable):
    engine, _, _, refs = executable
    operation = confirmed(engine, refs)
    token = claim(engine, refs)["lease_id"]
    request = RollbackRequest(operation_id=str(uuid4()), reason="Restore previous approved content")
    with Session(engine) as session:
        service = ExecutionService(session, clock=lambda: NOW+timedelta(seconds=1))
        rollback = service.queue_rollback(refs.operation_id, request, UserActor(ALICE))
        rollback_refs = ExecutionRequest.model_validate({key: rollback["record"][key] for key in ExecutionRequest.model_fields})
        lease = ExecutionPreparation(session, clock=lambda: NOW+timedelta(seconds=1)).claim(request.operation_id, UserActor(ALICE))["lease_id"]
        prepared = service.reserve(rollback_refs, UserActor(ALICE), BINDING, operation["record"]["after"], preparation_lease=lease)
        assert prepared["record"]["after"] == BEFORE
        ExecutionPreparation(session, clock=lambda: NOW+timedelta(seconds=1)).ready(request.operation_id, lease)
        service.begin(request.operation_id, UserActor(ALICE), preparation_lease=lease)
    with pytest.raises(ApprovalError, match="verification_lease_conflict"):
        report(engine, refs, token, operation, seconds=2)
    assert claim(engine, refs, 3)["operation"]["state"] == "rollback_in_progress"


def test_rollback_drift_fails_before_freeze_and_expired_approval_does_not_block_authorized_undo(executable):
    from copy import deepcopy
    engine, _, _, refs = executable
    operation = confirmed(engine, refs)
    now = NOW + timedelta(days=2)  # Original 24h approval has expired; ownership is still fresh.
    with Session(engine) as session:
        service = ExecutionService(session, clock=lambda: now)
        request = RollbackRequest(operation_id=str(uuid4()), reason="Undo after review")
        queued = service.queue_rollback(refs.operation_id, request, UserActor(ALICE))
        rollback_refs = ExecutionRequest.model_validate({key: queued["record"][key] for key in ExecutionRequest.model_fields})
        lease = ExecutionPreparation(session, clock=lambda: now).claim(request.operation_id, UserActor(ALICE))["lease_id"]
        changed = deepcopy(operation["record"]["after"])
        changed["fields"]["title"]["value"] = "Manual edit"
        with pytest.raises(ValueError, match="cms_precondition_conflict"):
            service.reserve(rollback_refs, UserActor(ALICE), BINDING, changed, preparation_lease=lease)
        restored = service.reserve(rollback_refs, UserActor(ALICE), BINDING, operation["record"]["after"], preparation_lease=lease)
        assert restored["record"]["after"] == BEFORE


def test_rollback_requires_current_deploy_permission_and_cannot_change_reason(executable):
    engine, _, _, refs = executable
    confirmed(engine, refs)
    request = RollbackRequest(operation_id=str(uuid4()), reason="Restore")
    with Session(engine) as session:
        service = ExecutionService(session, clock=lambda: NOW)
        service.queue_rollback(refs.operation_id, request, UserActor(ALICE))
        with pytest.raises(ApprovalError, match="idempotency"):
            service.queue_rollback(refs.operation_id, request.model_copy(update={"reason": "Different"}), UserActor(ALICE))
    with engine.begin() as conn:
        conn.execute(text("UPDATE audit_schema.workspace_members SET role='viewer' WHERE user_id=:id"), {"id": ALICE})
    with Session(engine) as session, pytest.raises(ApprovalError, match="permission_denied"):
        ExecutionService(session, clock=lambda: NOW).queue_rollback(refs.operation_id, request, UserActor(ALICE))


def test_migration_cannot_drop_verification_history_or_reset_budget(executable):
    from tests.integration.test_management_recovery_postgres import revision
    from services.management_service.migrations.schema import require_schema
    engine, _, _, refs = executable
    confirmed(engine, refs)
    with pytest.raises(RuntimeError, match="HITL_VERIFICATION_DOWNGRADE_WOULD_LOSE_HISTORY"):
        revision(engine, "downgrade", "20261114_006_preparation")
    with engine.connect() as conn:
        require_schema(conn)
    assert claim(engine, refs)["operation"]["verification"]["attempts"] == 1


def test_old_rollback_preserves_new_version_task_and_fences_competing_apply(executable):
    from services.management_service.approval_service import ApprovalService
    from services.management_service.approval_policy import DecisionRequest
    from services.management_service.models import HITLApproval
    from services.semantic_service.proposals.repository import row_from_version
    engine, version, _, refs = executable
    operation = confirmed(engine, refs)
    newer = version.model_copy(update={"version_id": str(uuid4()), "version": 2, "previous_version_id": version.version_id})
    with Session(engine) as session:
        # Simulate a later lifecycle after the old operation, with a real newly approved version.
        session.get(Task, UUID(TASK_ID)).status = TaskStatus.FAILED
        session.add(row_from_version(newer))
        session.commit()
        approvals = ApprovalService(session, clock=lambda: NOW)
        approval = approvals.prepare(newer, metadata={})
        approvals.decide(TASK_ID, UserActor(ALICE), DecisionRequest(approval_id=str(approval.id),
            recommendation_version_id=newer.version_id, content_hash=newer.content_hash), "approve")
    request = RollbackRequest(operation_id=str(uuid4()), reason="Restore earlier operation")
    with Session(engine) as session:
        # Warm the identity map with v2; operation results remain tied to v1.
        session.query(HITLApproval).first()
        service = ExecutionService(session, clock=lambda: NOW)
        queued = service.queue_rollback(refs.operation_id, request, UserActor(ALICE))
        rollback_refs = ExecutionRequest.model_validate({key: queued["record"][key] for key in ExecutionRequest.model_fields})
        prep = ExecutionPreparation(session, clock=lambda: NOW)
        lease = prep.claim(request.operation_id, UserActor(ALICE))["lease_id"]
        service.reserve(rollback_refs, UserActor(ALICE), BINDING, operation["record"]["after"], preparation_lease=lease)
        prep.ready(request.operation_id, lease)
        begun = service.begin(request.operation_id, UserActor(ALICE), preparation_lease=lease)
        assert session.get(Task, UUID(TASK_ID)).status == TaskStatus.APPROVED
        new_refs = refs.model_copy(update={"operation_id": str(uuid4()), "recommendation_version_id": newer.version_id, "content_hash": newer.content_hash})
        with pytest.raises(ApprovalError, match="other_operation_inflight"):
            service.queue(new_refs, UserActor(ALICE))
        service.finish(request.operation_id, receipt=receipt_for(begun))
        assert session.get(Task, UUID(TASK_ID)).status == TaskStatus.APPROVED
    assert reserve(engine, new_refs)["state"] == "ready"
    assert begin(engine, new_refs)["state"] == "applying"
