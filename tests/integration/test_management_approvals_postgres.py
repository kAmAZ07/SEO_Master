"""AR-4 PostgreSQL transactions on the checked Management migration line.

Core/Audit/Semantic tables remain isolated ORM fixtures, not full-stack migrations.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Barrier
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.integration.test_migrations_postgres import ADMIN_URL, TempDatabase
from database.models import User, Workspace, WorkspaceMember, Project as CanonicalProject
from services.audit_service.db.models import CrawlResult
from services.management_service.approval_policy import ApprovalError, DecisionRequest, UserActor
from services.management_service.approval_service import ApprovalService
from services.management_service.migrations.runner import migrate
from services.management_service.models import Base, HITLApproval, HITLStatus, Project, Task, TaskStatus, TaskType
from services.management_service.proposal_handoff import version_diff
from services.semantic_service.db.models import RecommendationVersionRow
from services.semantic_service.proposals.repository import row_from_version
from shared.page_evidence import seal_evidence
from tests.fixtures.management_proposal import PROJECT_ID, TASK_ID, TARGET_URL, management_proposal_fixture

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="TEST_PG_ADMIN_URL is not set")
NOW = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)
ALICE = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
BOB = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
WORKSPACE = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
APPROVAL = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"


@pytest.fixture()
def stand():
    with TempDatabase() as database:
        with database.engine.begin() as conn:
            conn.execute(text("CREATE SCHEMA audit_schema"))
            conn.execute(text("CREATE SCHEMA semantic_schema"))
            for table in (User.__table__, Workspace.__table__, WorkspaceMember.__table__, CanonicalProject.__table__):
                table.create(conn)
            for name in ("projects", "tasks", "hitl_approvals", "management_changelog"):
                Base.metadata.tables[name].create(conn)
            RecommendationVersionRow.__table__.create(conn)
            CrawlResult.__table__.create(conn)
            for remainder in range(8):
                conn.execute(text(f"CREATE TABLE audit_schema.crawl_results_p{remainder} "
                                  f"PARTITION OF audit_schema.crawl_results FOR VALUES WITH (MODULUS 8, REMAINDER {remainder})"))
        with database.engine.connect() as conn:
            migrate(conn, expected_role=database.engine.url.username or "")
        request, _, _ = management_proposal_fixture()
        envelope = seal_evidence({"schema_version": "page_evidence_v1", "audit_id": "source-audit",
                                  "project_id": PROJECT_ID, "url": TARGET_URL, "final_url": TARGET_URL,
                                  "status_code": 200, "facts": request["facts"], "jsonld_blocks": 0})
        _, _, version = management_proposal_fixture(evidence_override=[{
            "evidence_id": envelope["evidence_id"], "sha256": envelope["content_hash"],
        }])
        with Session(database.engine) as session:
            session.add_all([User(id=ALICE, email="alice@example.test", hashed_password="fixture", is_active=True),
                             User(id=BOB, email="bob@example.test", hashed_password="fixture", is_active=True),
                             Workspace(id=WORKSPACE, name="Fixture", owner_id=ALICE, kind="personal")])
            session.flush()
            session.add(WorkspaceMember(workspace_id=WORKSPACE, user_id=ALICE, role="reviewer"))
            session.add(CanonicalProject(id=PROJECT_ID, name="Canonical", url="https://example.com/",
                                         status="active", owner_id=ALICE, workspace_id=WORKSPACE,
                                         origin="https://example.com", path_prefix="/"))
            # Explicitly equal UUIDs in this fixture; no domain-based legacy mapping is used.
            session.add(Project(id=uuid.UUID(PROJECT_ID), name="Legacy FK fixture", domain="example.com"))
            session.flush()
            session.add(Task(id=uuid.UUID(TASK_ID), project_id=uuid.UUID(PROJECT_ID), url=TARGET_URL,
                             task_type=TaskType.UPDATE_META, status=TaskStatus.PENDING))
            session.flush()
            session.add(HITLApproval(id=uuid.UUID(APPROVAL), task_id=uuid.UUID(TASK_ID),
                                     project_id=uuid.UUID(PROJECT_ID), status=HITLStatus.PENDING,
                                     diff_data=version_diff(version), meta={}))
            session.add(row_from_version(version))
            session.add(CrawlResult(audit_id="source-audit", project_id=PROJECT_ID, root_url=TARGET_URL,
                                    mode="full", status="completed", created_at=NOW, updated_at=NOW,
                                    crawled_at=NOW, pages=[{"url": TARGET_URL, "page_evidence": envelope}]))
            session.commit()
        command = DecisionRequest(approval_id=APPROVAL, recommendation_version_id=version.version_id,
                                   content_hash=version.content_hash, comment="Reviewed exact version")
        yield database.engine, version, command


def decide(engine, command, *, action="approve", user=ALICE, now=NOW):
    with Session(engine) as session:
        return ApprovalService(session, clock=lambda: now).decide(TASK_ID, UserActor(user), command, action)


def state(engine, *, user=ALICE, now=NOW):
    with Session(engine) as session:
        service = ApprovalService(session, clock=lambda: now)
        approval, task = service.readable(TASK_ID, UserActor(user))
        return service.authorization_status(approval, task, UserActor(user))


def test_real_decision_binds_actor_role_version_scope_and_exact_24h_expiry(stand):
    engine, version, command = stand
    result = decide(engine, command)
    record = result["decision_record"]
    assert record["actor_id"] == ALICE and record["actor_role"] == "reviewer"
    assert record["workspace_id"] == WORKSPACE and record["recommendation_version_id"] == version.version_id
    assert record["recommendation_content_hash"] == version.content_hash
    assert result["auto_deployed"] is False and result["publication_status"] == "not_requested"
    assert state(engine) == "approved"
    assert state(engine, now=NOW + timedelta(hours=24, microseconds=-1)) == "approved"
    assert state(engine, now=NOW + timedelta(hours=24)) == "expired"


def test_decision_and_event_are_committed_in_one_transaction(stand):
    engine, version, command = stand
    result = decide(engine, command)
    with engine.connect() as conn:
        assert conn.scalar(text("SELECT to_regclass('public.hitl_history')")) is not None
        assert conn.scalar(text("SELECT to_regclass('public.management_outbox')")) is not None
        history = conn.execute(text("SELECT entry_type, version_id, metadata FROM public.hitl_history")).mappings().one()
        event = conn.execute(text("SELECT status, metadata FROM public.management_outbox")).mappings().one()
    assert history["entry_type"] == "approve" and history["version_id"] == version.version_id
    assert history["metadata"]["decision_hash"] == result["approval_hash"]
    assert event["status"] == "pending"
    assert event["metadata"]["payload"]["approval_hash"] == result["approval_hash"]
    assert event["metadata"]["payload"]["auto_deployed"] is False


@pytest.mark.parametrize("action", ["approve", "reject", "revoke"])
def test_foreign_user_cannot_decide_read_or_discover_task(stand, action):
    engine, _, command = stand
    with pytest.raises(ApprovalError) as error:
        decide(engine, command, action=action, user=BOB)
    assert error.value.http_status == 404
    with pytest.raises(ApprovalError) as error:
        state(engine, user=BOB)
    assert error.value.http_status == 404
    assert state(engine) == "pending"


@pytest.mark.parametrize("role", ["viewer", "deployer"])
def test_read_permission_does_not_allow_decision(stand, role):
    engine, _, command = stand
    with engine.begin() as conn:
        conn.execute(text("UPDATE audit_schema.workspace_members SET role = :role WHERE user_id = :actor"),
                     {"role": role, "actor": ALICE})
    with pytest.raises(ApprovalError) as error:
        decide(engine, command)
    assert error.value.http_status == 403
    assert state(engine) == "pending"


def test_competing_approve_reject_have_one_commit_and_one_conflict(stand):
    engine, _, command = stand
    barrier = Barrier(2)

    def run(action):
        barrier.wait(timeout=5)
        try:
            return decide(engine, command, action=action)["status"]
        except ApprovalError as error:
            assert error.http_status == 409
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, ["approve", "reject"]))
    assert results.count("conflict") == 1
    assert state(engine) in {"approved", "rejected"}


@pytest.mark.parametrize("field,value", [("approval_id", BOB), ("recommendation_version_id", BOB),
                                        ("content_hash", "0" * 64)])
def test_changed_browser_reference_cannot_approve_another_preview(stand, field, value):
    engine, _, command = stand
    altered = DecisionRequest.model_validate({**command.model_dump(), field: value})
    with pytest.raises(ApprovalError) as error:
        decide(engine, altered)
    assert error.value.http_status == 409
    assert state(engine) == "pending"


@pytest.mark.parametrize("mutation", ["version", "target", "evidence", "preview", "role", "disabled_user"])
def test_stale_or_modified_authorization_never_remains_approved(stand, mutation):
    engine, version, command = stand
    decide(engine, command)
    with Session(engine) as session:
        if mutation == "version":
            newer = version.model_copy(update={"version_id": str(uuid.uuid4()), "version": 2,
                                                "previous_version_id": version.version_id})
            session.add(row_from_version(newer))
        elif mutation == "target":
            task = session.get(Task, uuid.UUID(TASK_ID))
            task.url += "/other"
        elif mutation == "evidence":
            row = session.query(CrawlResult).filter_by(audit_id="source-audit", project_id=PROJECT_ID).one()
            row.pages = []
        elif mutation == "preview":
            row = session.get(HITLApproval, uuid.UUID(APPROVAL))
            row.diff_data = {**row.diff_data, "after": {"head.title": "unreviewed"}}
        elif mutation == "role":
            member = session.get(WorkspaceMember, (WORKSPACE, ALICE))
            member.role = "viewer"
        else:
            session.get(User, ALICE).is_active = False
        session.commit()
    if mutation == "disabled_user":
        with pytest.raises(ApprovalError):
            state(engine)
    else:
        assert state(engine) == "invalidated"


def test_revoke_keeps_immutable_original_decision_and_blocks_reapproval(stand):
    engine, _, command = stand
    first = decide(engine, command)
    revoked = decide(engine, command, action="revoke")
    assert revoked["decision_record"] == first["decision_record"]
    assert revoked["approval_hash"] == first["approval_hash"]
    assert state(engine) == "revoked"
    with pytest.raises(ApprovalError):
        decide(engine, command)


@pytest.mark.parametrize("status", [TaskStatus.IN_PROGRESS, TaskStatus.DEPLOYED])
def test_revocation_cannot_change_decision_during_or_after_execution(stand, status):
    engine, _, command = stand
    first = decide(engine, command)
    with Session(engine) as session:
        session.get(Task, uuid.UUID(TASK_ID)).status = status
        session.commit()
    with pytest.raises(ApprovalError) as error:
        decide(engine, command, action="revoke")
    assert error.value.http_status == 409
    with Session(engine) as session:
        row = session.get(HITLApproval, uuid.UUID(APPROVAL))
        assert row.meta["decision_hash"] == first["approval_hash"]
        assert "revocation" not in row.meta


def test_cached_pending_orm_state_cannot_overwrite_committed_decision(stand):
    engine, _, command = stand
    with Session(engine) as stale_session:
        stale_approval = stale_session.get(HITLApproval, uuid.UUID(APPROVAL))
        assert stale_approval.status == HITLStatus.PENDING
        decide(engine, command)
        with pytest.raises(ApprovalError) as error:
            ApprovalService(stale_session, clock=lambda: NOW).decide(TASK_ID, UserActor(ALICE), command, "reject")
        assert error.value.http_status == 409
    assert state(engine) == "approved"


def test_lost_commit_response_is_reconciled_without_a_second_decision(stand, monkeypatch):
    engine, _, command = stand
    with Session(engine) as session:
        original_commit = session.commit

        def commit_then_disconnect():
            original_commit()
            raise ConnectionError("synthetic loss after server commit")

        monkeypatch.setattr(session, "commit", commit_then_disconnect)
        with pytest.raises(ConnectionError):
            ApprovalService(session, clock=lambda: NOW).decide(TASK_ID, UserActor(ALICE), command, "approve")
    assert state(engine) == "approved"
    with pytest.raises(ApprovalError) as error:
        decide(engine, command)
    assert error.value.http_status == 409
