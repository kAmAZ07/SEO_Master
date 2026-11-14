"""Status-only recovery boundaries, independent of a user session or new command."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

from fastapi import HTTPException
import pytest

from services.client_api_gateway import recovery
from services.client_api_gateway.hitl_executor import StatusReconciler, authority_request
from shared.hitl_execution import ExecutionError


def operation():
    return {"state": "outcome_unknown", "record": {"operation_id": str(uuid4()), "project_id": str(uuid4()),
        "binding": {"base_url": "https://example.com", "site_id": str(uuid4()), "key_id": "current"}}}


@pytest.mark.asyncio
@pytest.mark.parametrize("error,expected", [
    (ExecutionError("cms_operation_not_found"), "operation_not_found"),
    (ExecutionError("cms_receipt_signature_invalid"), "receipt_invalid"),
    (ExecutionError("cms_response_invalid"), "receipt_invalid"),
    (ExecutionError("cms_request_rejected:401"), "integration_changed"),
    (TimeoutError("secret URL must not be reported"), "cms_unavailable"),
])
async def test_claim_precedes_status_and_sanitized_report_never_calls_apply(error, expected):
    value, token = operation(), str(uuid4())
    authority = AsyncMock(side_effect=[{"operation": value, "lease_id": token}, value])
    target = SimpleNamespace(status=AsyncMock(side_effect=error), apply=AsyncMock(), preflight=AsyncMock())
    async def status(record):
        assert authority.await_count == 1
        raise error
    target.status.side_effect = status
    assert await StatusReconciler(authority=authority, target_factory=lambda *args: target).once() == value
    assert authority.await_args_list[0].args == ("POST", "/recovery/claim", {"operation_id": None})
    assert authority.await_args_list[-1].args[-1] == {"lease_id": token, "receipt": None, "error_code": expected}
    target.apply.assert_not_called()
    target.preflight.assert_not_called()


@pytest.mark.asyncio
async def test_no_lease_means_no_cms_request_or_budget_reset():
    value = operation()
    authority = AsyncMock(return_value={"operation": value, "lease_id": None})
    target = Mock(side_effect=AssertionError("CMS reached without persisted attempt"))
    assert await StatusReconciler(authority=authority, target_factory=target).once(value["record"]["operation_id"]) == value
    target.assert_not_called()
    assert authority.await_count == 1


@pytest.mark.asyncio
async def test_integration_rotation_reports_changed_binding_without_http():
    value, token = operation(), str(uuid4())
    authority = AsyncMock(side_effect=[{"operation": value, "lease_id": token}, value])
    target = Mock(side_effect=HTTPException(409, "binding changed"))
    await StatusReconciler(authority=authority, target_factory=target).once()
    assert authority.await_args.args[-1]["error_code"] == "integration_changed"


@pytest.mark.asyncio
async def test_cancelled_worker_leaves_lease_to_expire_without_false_report():
    value = operation()
    authority = AsyncMock(return_value={"operation": value, "lease_id": str(uuid4())})
    target = SimpleNamespace(status=AsyncMock(side_effect=asyncio.CancelledError()))
    with pytest.raises(asyncio.CancelledError):
        await StatusReconciler(authority=authority, target_factory=lambda *args: target).once()
    assert authority.await_count == 1


@pytest.mark.asyncio
async def test_recovery_service_request_has_both_service_keys_and_no_user_token(monkeypatch):
    import httpx
    from services.client_api_gateway import hitl_executor
    seen = []
    def response(request):
        seen.append(request)
        return httpx.Response(200, json={"operation": None, "lease_id": None})
    original = httpx.AsyncClient
    monkeypatch.setattr(hitl_executor.httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(response), **kw))
    monkeypatch.setattr(hitl_executor.settings, "INTERNAL_API_KEY", "i" * 32)
    monkeypatch.setattr(hitl_executor.settings, "MANAGEMENT_EXECUTOR_API_KEY", "e" * 32)
    await authority_request("POST", "/recovery/claim", {"operation_id": None})
    assert "authorization" not in seen[0].headers
    assert seen[0].headers["x-internal-api-key"] == "i" * 32
    assert seen[0].headers["x-executor-key"] == "e" * 32


@pytest.mark.asyncio
async def test_lifecycle_stops_its_worker_and_disabled_config_starts_nothing(monkeypatch):
    monkeypatch.setattr(recovery.settings, "EXECUTION_RECOVERY_ENABLED", False)
    assert recovery.start() is None
    monkeypatch.setattr(recovery.settings, "EXECUTION_RECOVERY_ENABLED", True)
    monkeypatch.setattr(recovery.settings, "INTERNAL_API_KEY", "i" * 32)
    monkeypatch.setattr(recovery.settings, "MANAGEMENT_EXECUTOR_API_KEY", "e" * 32)
    started = asyncio.Event()
    async def run():
        started.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(recovery, "run", run)
    task = recovery.start()
    await asyncio.wait_for(started.wait(), 1)
    await recovery.stop(task)
    assert task.cancelled()


@pytest.mark.parametrize("field,value", [("entity_id", True), ("warnings", "warning"), ("warnings", [False]),
    ("plugin_version", None), ("applied_at", "2026-02-30T00:00:00Z"), ("signature", "unsigned"), ("before", None)])
def test_structurally_invalid_receipt_cannot_complete_even_before_signature_check(field, value):
    from shared.hitl_execution import validate_receipt
    required = {"protocol", "kind", "receipt_id", "operation_id", "project_id", "site_id", "entity_id", "task_id",
        "proposal_version", "proposal_hash", "approval_ref", "request_hash", "before_digest", "after_digest", "before", "after",
        "plugin_version", "key_id", "applied_at", "applied_fields", "outcome", "warnings", "signature"}
    receipt = dict.fromkeys(required, "fixture")
    receipt.update(entity_id=1, warnings=[], plugin_version="0.3.0-dev", key_id="current", applied_at="2026-11-14T00:00:00Z", signature="a" * 64)
    receipt[field] = value
    with pytest.raises(ExecutionError, match="cms_receipt_invalid"):
        validate_receipt(receipt, {}, {}, {})


@pytest.mark.parametrize("enabled,keys,task,expected", [
    (False, False, None, 200), (True, False, None, 503), (True, True, None, 503),
    (True, True, SimpleNamespace(done=lambda: False), 200),
    (True, True, SimpleNamespace(done=lambda: True), 503),
])
def test_readiness_distinguishes_explicit_disable_from_broken_recovery(monkeypatch, enabled, keys, task, expected):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from services.client_api_gateway import db
    from services.client_api_gateway.routes.health import router
    monkeypatch.setattr(db, "init_db", Mock())
    monkeypatch.setattr(recovery.settings, "EXECUTION_RECOVERY_ENABLED", enabled)
    monkeypatch.setattr(recovery.settings, "INTERNAL_API_KEY", "i" * 32 if keys else "")
    monkeypatch.setattr(recovery.settings, "MANAGEMENT_EXECUTOR_API_KEY", "e" * 32 if keys else "")
    app = FastAPI()
    app.include_router(router)
    app.state.execution_recovery_task = task
    with TestClient(app) as client:
        assert client.get("/health/ready").status_code == expected
