"""Authenticated decisions, immutable bindings and expiry boundaries."""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from services.management_service.approval_policy import (
    ApprovalError, DecisionRequest, UserActor, make_record, record_state,
)
from tests.fixtures.management_proposal import PROJECT_ID, TASK_ID, management_proposal_fixture

NOW = datetime(2026, 11, 9, 12, tzinfo=timezone.utc)


def record_fixture():
    _, _, version = management_proposal_fixture()
    record = make_record(approval_id=TASK_ID, task_id=TASK_ID, workspace_id=PROJECT_ID,
                         version=version, actor_id=TASK_ID, actor_role="reviewer", decision="approve",
                         now=NOW, ttl=timedelta(hours=24))
    return version, record, {"decision_record": record.model_dump(mode="json"), "decision_hash": record.digest()}


@pytest.mark.parametrize("offset,expected", [(timedelta(0), "approved"),
    (timedelta(hours=24, microseconds=-1), "approved"), (timedelta(hours=24), "expired"),
    (timedelta(microseconds=-1), "expired")])
def test_approval_expires_at_exact_boundary(offset, expected):
    version, _, metadata = record_fixture()
    assert record_state(metadata, version=version, now=NOW + offset) == expected


@pytest.mark.parametrize("field", ["actor_id", "actor_role", "workspace_id", "target_url",
                                  "recommendation_content_hash", "expires_at"])
def test_decision_hash_binds_every_authorization_field(field):
    version, _, metadata = record_fixture()
    metadata["decision_record"][field] = "tampered"
    assert record_state(metadata, version=version, now=NOW) == "invalidated"


def test_revocation_blocks_approved_version_without_rewriting_original_record():
    version, record, metadata = record_fixture()
    metadata["revocation"] = {"actor_id": TASK_ID, "revoked_at": NOW.isoformat()}
    assert record_state(metadata, version=version, now=NOW) == "revoked"
    assert metadata["decision_record"] == record.model_dump(mode="json")
    metadata["decision_comment"] = "Comment cannot change the payload"
    assert metadata["decision_hash"] == record.digest()


def test_decision_request_rejects_body_identity_and_missing_version():
    with pytest.raises(ValidationError):
        DecisionRequest(approval_id=TASK_ID, recommendation_version_id=PROJECT_ID,
                        content_hash="a" * 64, user_id="forged")
    with pytest.raises(ValidationError):
        DecisionRequest(approval_id=TASK_ID, content_hash="a" * 64)


@pytest.mark.parametrize("status,body,expected", [(200, {"id": TASK_ID}, TASK_ID),
    (401, {}, 401), (403, {}, 401), (500, {}, 503), (200, {"id": "invalid"}, 503)])
def test_identity_is_resolved_by_existing_session_authority_not_request_body(monkeypatch, status, body, expected):
    from services.management_service import approval_identity as identity
    response = httpx.Response(status, json=body)
    get = AsyncMock(return_value=response)

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, *args, **kwargs):
            return await get(*args, **kwargs)

    factory = Mock(return_value=Client())
    monkeypatch.setattr(identity.httpx, "AsyncClient", factory)
    if isinstance(expected, str):
        assert asyncio.run(identity.authenticated_actor("Bearer fixture-access", None)) == UserActor(expected)
    else:
        with pytest.raises(HTTPException) as error:
            asyncio.run(identity.authenticated_actor("Bearer fixture-access", None))
        assert error.value.status_code == expected
    assert get.call_args.kwargs["headers"] == {"Authorization": "Bearer fixture-access"}
    assert factory.call_args.kwargs["follow_redirects"] is False


@pytest.mark.parametrize("header", [None, "", "forged-key"])
def test_internal_header_does_not_authenticate_a_user_or_allow_invalid_service_key(monkeypatch, header):
    from services.management_service import approval_identity as identity
    monkeypatch.setattr(identity.settings, "INTERNAL_API_KEY", "k" * 32)
    with pytest.raises(HTTPException) as error:
        identity.require_service_identity(header)
    assert error.value.status_code == 401
    with pytest.raises(HTTPException) as error:
        asyncio.run(identity.authenticated_actor(None, None))
    assert error.value.status_code == 401


def test_legacy_adapter_cannot_publish_task_metadata_even_when_status_is_approved(monkeypatch):
    from services.management_service import client_api_adapter as adapter
    from services.management_service.models import TaskStatus, TaskType

    transport = AsyncMock(return_value={"status": "success", "change_id": "unapproved"})
    monkeypatch.setattr(adapter, "ClientAPIAdapter", Mock(return_value=SimpleNamespace(deploy_changes=transport)))
    task = SimpleNamespace(id=TASK_ID, project_id=PROJECT_ID, url="https://example.com/catalog",
                           status=TaskStatus.APPROVED, task_type=TaskType.UPDATE_META,
                           meta={"changes": {"before": {}, "after": {"title": "arbitrary"}}},
                           impact_score=0.5, effort_score=0.5, priority_score=0.5)
    db = Mock()
    with pytest.raises(ApprovalError, match="version_bound_executor_required"):
        asyncio.run(adapter.deploy_task_changes(db, task))
    transport.assert_not_called()
    db.commit.assert_not_called()
    assert task.status == TaskStatus.APPROVED


def test_arbitrary_payload_cannot_use_the_old_adapter_without_authorization():
    from services.management_service.client_api_adapter import ClientAPIAdapter
    adapter = object.__new__(ClientAPIAdapter)
    with pytest.raises(ApprovalError, match="version_bound_executor_required"):
        asyncio.run(adapter.deploy_changes(TASK_ID, {"changes": {"after": {"title": "unreviewed"}}}))


def test_worker_notification_cannot_become_a_new_human_decision(monkeypatch):
    from services.management_service import hitl_handler
    approve = AsyncMock(return_value={"status": "approved"})
    monkeypatch.setattr(hitl_handler, "HITLHandler", Mock(return_value=SimpleNamespace(approve_task=approve)))
    with pytest.raises(ApprovalError, match="human_decision_required"):
        asyncio.run(hitl_handler.handle_hitl_approved_event(Mock(), {"payload": {
            "task_id": TASK_ID, "approved_by": "forged-event-actor", "auto_deployed": False,
        }}))
    approve.assert_not_called()


def test_management_startup_refuses_missing_migrations_without_creating_tables(monkeypatch):
    from contextlib import nullcontext
    from services.management_service.db import session
    from services.management_service.models import Base
    connection = SimpleNamespace(scalar=Mock(return_value=None))
    engine = SimpleNamespace(connect=Mock(return_value=nullcontext(connection)))
    ddl = Mock(side_effect=AssertionError("Startup must not create application tables"))
    monkeypatch.setattr(session, "engine", engine)
    monkeypatch.setattr(Base.metadata, "create_all", ddl)
    with pytest.raises(RuntimeError, match="MANAGEMENT_SCHEMA_NOT_READY"):
        session.init_db()
    ddl.assert_not_called()


@pytest.mark.parametrize("operation", ["drop_db", "reset_db"])
def test_management_runtime_schema_reset_is_forbidden(monkeypatch, operation):
    from services.management_service.db import session

    connection = Mock(side_effect=AssertionError("Runtime DDL must never connect"))
    monkeypatch.setattr(session, "engine", SimpleNamespace(connect=connection))
    with pytest.raises(RuntimeError, match="MANAGEMENT_RUNTIME_DDL_FORBIDDEN"):
        getattr(session, operation)()
    connection.assert_not_called()


def test_management_health_rejects_live_connection_without_migration_stamp(monkeypatch):
    from contextlib import nullcontext
    from services.management_service.db import session

    connection = SimpleNamespace(scalar=Mock(return_value=None))
    monkeypatch.setattr(session, "engine", SimpleNamespace(connect=lambda: nullcontext(connection)))
    assert session.get_db_health() == {
        "status": "unhealthy", "database": "disconnected", "error": "MANAGEMENT_DATABASE_NOT_READY",
    }


def test_management_readiness_requires_schema_health_even_when_broker_is_ready(monkeypatch):
    from unittest.mock import AsyncMock
    from services.management_service import main

    monkeypatch.setattr(main, "get_db_health", lambda: {"status": "unhealthy"})
    monkeypatch.setattr(main, "consumers_are_running", lambda: True)
    monkeypatch.setattr(main, "check_rabbitmq_connection", AsyncMock(return_value=True))
    response = asyncio.run(main.readiness_check())
    assert response.status_code == 503


def test_management_runner_has_no_application_dsn_fallback(monkeypatch, capsys):
    from services.management_service.migrations import runner

    monkeypatch.setattr("sys.argv", ["runner", "upgrade", "--url-env", "EXPLICIT_TEST_MIGRATION_DSN",
                                    "--expected-role", "management_migrator"])
    monkeypatch.delenv("EXPLICIT_TEST_MIGRATION_DSN", raising=False)
    engine = Mock(side_effect=AssertionError("Missing explicit DSN must not create engine"))
    monkeypatch.setattr(runner, "create_engine", engine)
    assert runner.main() == 1
    assert "MANAGEMENT_MIGRATION_DSN_REQUIRED" in capsys.readouterr().out
    engine.assert_not_called()


def test_management_runner_never_prints_vendor_errors_or_dsn(monkeypatch, capsys):
    from services.management_service.migrations import runner

    secret = "private-migration-password"
    monkeypatch.setattr("sys.argv", ["runner", "status", "--url-env", "EXPLICIT_TEST_MIGRATION_DSN",
                                    "--expected-role", "management_migrator"])
    monkeypatch.setenv("EXPLICIT_TEST_MIGRATION_DSN", "postgresql://actor:" + secret + "@localhost/fixture")
    monkeypatch.setattr(runner, "create_engine", Mock(side_effect=ValueError(secret)))
    assert runner.main() == 1
    output = capsys.readouterr().out
    assert "MANAGEMENT_MIGRATION_FAILED" in output and secret not in output


@pytest.mark.parametrize("method,path", [("GET", ""), ("POST", "/"), ("GET", "/old-id"),
                                        ("PATCH", "/old-id"), ("DELETE", "/old-id")])
def test_legacy_management_project_crud_is_retired_without_database_access(method, path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from services.management_service.api.endpoints import projects

    app = FastAPI()
    app.include_router(projects.router, prefix="/api/v1/projects")
    response = TestClient(app).request(method, "/api/v1/projects" + path, json={"domain": "example.com"})
    assert response.status_code == 410
    assert response.json()["detail"] == "canonical_projects_are_managed_by_api_gateway"


def test_internal_project_lookup_requires_service_identity_and_uses_core():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from services.management_service.api.endpoints import internal
    from services.management_service.db.session import get_db
    from services.management_service.config import settings

    db = SimpleNamespace(scalar=Mock(return_value=True))
    app = FastAPI()
    app.include_router(internal.router, prefix="/internal")
    app.dependency_overrides[get_db] = lambda: db
    client = TestClient(app)
    assert client.get("/internal/projects/project-id/exists").status_code == 401
    db.scalar.assert_not_called()
    headers = {"X-Internal-API-Key": settings.INTERNAL_API_KEY}
    assert client.get("/internal/projects/project-id/exists", headers=headers).status_code == 200
    assert "audit_schema.projects" in str(db.scalar.call_args.args[0])
    db.scalar.return_value = False
    assert client.get("/internal/projects/project-id/exists", headers=headers).status_code == 404


def test_task_api_rejects_unauthenticated_reads_before_querying_data():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from services.management_service.api.endpoints import tasks
    from services.management_service.db.session import get_db
    from unittest.mock import MagicMock

    db = MagicMock()
    db.query.return_value.filter.return_value.order_by.return_value.offset.return_value.limit.return_value.all.return_value = []
    app = FastAPI()
    app.include_router(tasks.router, prefix="/api/v1/tasks")
    app.dependency_overrides[get_db] = lambda: db
    response = TestClient(app, raise_server_exceptions=False).get("/api/v1/tasks")
    assert response.status_code == 401
    db.query.assert_not_called()
