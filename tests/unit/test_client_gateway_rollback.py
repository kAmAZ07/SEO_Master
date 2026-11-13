from types import SimpleNamespace
from uuid import uuid4

import pytest

from services.client_api_gateway import deployment_dispatcher as dispatcher


def test_normalize_changes_builds_reversible_patch_ops():
    changes = {
        "before": {
            "title": "Old title",
            "description": "Old description",
        },
        "after": {
            "title": "New title",
        },
    }

    ops = dispatcher._normalize_changes("meta", changes)

    assert ops == [
        {
            "op": "replace",
            "path": "/title",
            "value": "New title",
            "old_value": "Old title",
        },
        {
            "op": "remove",
            "path": "/description",
            "old_value": "Old description",
        },
    ]


def test_build_reverse_patch_ops_restores_previous_state_in_reverse_order():
    ops = [
        {"op": "add", "path": "/title", "value": "New title"},
        {
            "op": "replace",
            "path": "/description",
            "value": "New description",
            "old_value": "Old description",
        },
        {"op": "remove", "path": "/h1", "old_value": "Old H1"},
    ]

    assert dispatcher._build_reverse_patch_ops(ops) == [
        {"op": "add", "path": "/h1", "value": "Old H1"},
        {"op": "replace", "path": "/description", "value": "Old description"},
        {"op": "remove", "path": "/title"},
    ]


def test_target_platform_uses_metadata_then_entity_type():
    assert dispatcher._target_platform("web_page", {"platform": "wordpress"}) == "wordpress"
    assert dispatcher._target_platform("web_page", {"target_platform": "tilda"}) == "tilda"
    assert dispatcher._target_platform("tilda_page", {}) == "tilda"
    assert dispatcher._target_platform("web_page", {}) == "wordpress"


@pytest.mark.asyncio
async def test_legacy_rollback_cannot_dispatch_even_saved_changes(monkeypatch):
    deployment_id = uuid4()
    calls = []

    async def fake_dispatch_change(**kwargs):
        calls.append(kwargs)
        return {"status": "applied", "changes": kwargs["changes"]}

    monkeypatch.setattr(dispatcher, "dispatch_change", fake_dispatch_change)

    deployment_log = SimpleNamespace(
        id=deployment_id,
        project_id="project-1",
        entity_id="page-1",
        entity_type="web_page",
        change_type="meta",
        changes=[
            {
                "op": "replace",
                "path": "/title",
                "value": "New title",
                "old_value": "Old title",
            }
        ],
        meta={
            "platform": "wordpress",
            "rollback_changes": [
                {"op": "replace", "path": "/title", "value": "Old title"}
            ],
        },
    )

    from fastapi import HTTPException
    with pytest.raises(HTTPException) as error:
        await dispatcher.rollback_change(db=object(), deployment_log=deployment_log, correlation_id="corr-1")
    assert error.value.status_code == 409
    assert error.value.detail == "version_bound_rollback_required"
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("state", ["applying", "outcome_unknown", "needs_reconciliation", "applied_unverified", "no_op"])
async def test_repeated_execution_never_blindly_sends_apply(state):
    from unittest.mock import AsyncMock
    from services.client_api_gateway.hitl_executor import HITLExecutor
    from shared.hitl_execution import ExecutionError, ExecutionRequest
    refs = ExecutionRequest(operation_id=str(uuid4()), task_id=str(uuid4()), approval_id=str(uuid4()),
                            recommendation_version_id=str(uuid4()), content_hash="a" * 64)
    record = {**refs.model_dump(), "project_id": str(uuid4()), "binding": {
        "base_url": "https://example.com", "site_id": str(uuid4()), "key_id": "current"}}
    operation = {"state": state, "record": record}
    token = str(uuid4())
    authority = AsyncMock(side_effect=[operation, {"operation": operation, "lease_id": token if state != "needs_reconciliation" else None}, operation])
    target = SimpleNamespace(status=AsyncMock(side_effect=ExecutionError("cms_operation_not_found")),
                             apply=AsyncMock(), preflight=AsyncMock())
    executor = HITLExecutor("Bearer fixture", authority=authority, target_factory=lambda *args: target)
    assert await executor.execute(refs) == operation
    target.apply.assert_not_called()
    target.preflight.assert_not_called()
    assert target.status.call_count == (1 if state in {"applying", "outcome_unknown"} else 0)


@pytest.mark.asyncio
async def test_authority_rejection_after_preflight_leaves_cms_untouched():
    from fastapi import HTTPException
    from unittest.mock import AsyncMock
    from services.client_api_gateway.hitl_executor import HITLExecutor
    from shared.hitl_execution import ExecutionRequest
    refs = ExecutionRequest(operation_id=str(uuid4()), task_id=str(uuid4()), approval_id=str(uuid4()),
                            recommendation_version_id=str(uuid4()), content_hash="a" * 64)
    record = {**refs.model_dump(), "project_id": str(uuid4()), "version": {}, "before": {"exact": "snapshot"},
              "binding": {"base_url": "https://example.com", "site_id": str(uuid4()), "key_id": "current"}}
    operation = {"state": "ready", "record": record}
    authority = AsyncMock(side_effect=[operation, HTTPException(409, "execution_expired")])
    target = SimpleNamespace(preflight=AsyncMock(return_value=record["before"]), dry_run=AsyncMock(), apply=AsyncMock())
    executor = HITLExecutor("Bearer fixture", authority=authority, target_factory=lambda *args: target)
    with pytest.raises(HTTPException) as error:
        await executor.execute(refs)
    assert error.value.detail == "execution_expired"
    target.apply.assert_not_called()
    assert authority.call_args.args == ("POST", "/" + refs.operation_id + "/begin")


@pytest.mark.asyncio
async def test_lost_apply_response_marks_unknown_and_retries_only_status():
    from unittest.mock import AsyncMock
    from services.client_api_gateway.hitl_executor import HITLExecutor
    from shared.hitl_execution import ExecutionRequest
    refs = ExecutionRequest(operation_id=str(uuid4()), task_id=str(uuid4()), approval_id=str(uuid4()),
                            recommendation_version_id=str(uuid4()), content_hash="a" * 64)
    record = {**refs.model_dump(), "project_id": str(uuid4()), "version": {}, "before": {},
              "binding": {"base_url": "https://example.com", "site_id": str(uuid4()), "key_id": "current"}}
    ready = {"state": "ready", "record": record}
    applying = {**ready, "state": "applying"}
    unknown = {**ready, "state": "outcome_unknown"}
    final = {**ready, "state": "applied_unverified"}
    receipt = {"signed": "receipt"}
    token = str(uuid4())
    authority = AsyncMock(side_effect=[ready, applying, unknown, unknown, {"operation": unknown, "lease_id": token}, final])
    target = SimpleNamespace(preflight=AsyncMock(return_value={}), dry_run=AsyncMock(),
                             apply=AsyncMock(side_effect=TimeoutError()), status=AsyncMock(return_value=receipt))
    executor = HITLExecutor("Bearer fixture", authority=authority, target_factory=lambda *args: target)
    with pytest.raises(TimeoutError):
        await executor.execute(refs)
    assert authority.call_args.args == ("POST", "/" + refs.operation_id + "/finish", {"receipt": None})
    assert await executor.execute(refs) == final
    assert target.apply.call_count == 1 and target.status.call_count == 1
    assert authority.call_args.args == ("POST", "/recovery/" + refs.operation_id + "/report", {"lease_id": token, "receipt": receipt, "error_code": None})
