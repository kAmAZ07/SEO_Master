"""Pre-dispatch failures have a durable operation before the first CMS call."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from fastapi import HTTPException
import pytest

from services.client_api_gateway.hitl_executor import HITLExecutor
from shared.hitl_execution import ExecutionRequest, WordPressBinding


@pytest.mark.asyncio
async def test_initial_snapshot_failure_is_recorded_before_any_apply():
    refs = ExecutionRequest(operation_id=str(uuid4()), task_id=str(uuid4()), approval_id=str(uuid4()),
        recommendation_version_id=str(uuid4()), content_hash="a" * 64)
    version = {"project_id": str(uuid4())}
    value = {"operation_id": refs.operation_id, "state": "queued", "prepared": False, "record": {**refs.model_dump(), **version, "version": version}}
    calls = []
    token = str(uuid4())
    async def authority(method, path, payload=None):
        calls.append((method, path, payload))
        if method == "GET":
            raise HTTPException(404, "execution_not_found")
        if path == "/context":
            return version  # Old path reproduces the missing durable operation.
        if path == "/queue":
            return value
        if path.endswith("/preparation/claim"):
            return {"operation": {**value, "state": "preflight"}, "lease_id": token}
        if path.endswith("/preparation/fail"):
            return {**value, "state": "failed_before_apply"}
        raise AssertionError("unexpected authority command")
    async def preflight(_):
        assert any(path == "/queue" for _, path, _ in calls), "first CMS I/O has no durable operation"
        raise TimeoutError("CMS unavailable")
    target = SimpleNamespace(preflight=AsyncMock(side_effect=preflight), apply=AsyncMock())
    executor = HITLExecutor("Bearer fixture", authority=authority, target_factory=lambda *args: target)
    result = await executor.execute(refs)
    assert result["state"] == "failed_before_apply"
    assert calls[-1][1].endswith("/preparation/fail")
    assert calls[-1][2] == {"lease_id": token, "error_code": "cms_unavailable"}
    target.apply.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_begin", ["lost_response", "wrong_state", "replaced_record", "typed_change"])
async def test_ambiguous_or_invalid_begin_response_never_dispatches_apply_or_downgrades_begun_operation(bad_begin):
    refs = ExecutionRequest(operation_id=str(uuid4()), task_id=str(uuid4()), approval_id=str(uuid4()),
        recommendation_version_id=str(uuid4()), content_hash="a" * 64)
    binding = WordPressBinding(base_url="https://example.com", site_id=str(uuid4()), key_id="current")
    record = {**refs.model_dump(), "project_id": str(uuid4()), "binding": binding.model_dump(), "version": {"version": 1}, "before": {}}
    value = {"state": "ready", "record": record, "record_hash": "b" * 64}
    token = str(uuid4())
    calls = []
    async def authority(method, path, payload=None):
        calls.append(path)
        if method == "GET":
            return value
        if path.endswith("/preparation/claim"):
            return {"operation": {**value, "state": "preflight"}, "lease_id": token}
        if path == "/reserve":
            return {**value, "state": "dry_run"}
        if path.endswith("/preparation/ready"):
            return value
        if path.endswith("/begin"):
            if bad_begin == "lost_response":
                raise HTTPException(503, "execution_authority_unavailable")
            return {**value, "state": "ready" if bad_begin == "wrong_state" else "applying",
                "record": {**record, "actor_id": "replaced"} if bad_begin == "replaced_record" else
                    {**record, "version": {"version": True}} if bad_begin == "typed_change" else record}
        if path.endswith("/preparation/fail"):
            raise HTTPException(409, "preparation_lease_conflict")  # Real Management protects begun state.
        raise AssertionError("unexpected command")
    target = SimpleNamespace(binding=binding, preflight=AsyncMock(return_value={}), dry_run=AsyncMock(), apply=AsyncMock())
    with pytest.raises(HTTPException) as caught:
        await HITLExecutor("Bearer fixture", authority=authority, target_factory=lambda *args: target).execute(refs)
    assert caught.value.status_code == (503 if bad_begin == "lost_response" else 502)
    target.apply.assert_not_called()
    assert calls[-1].endswith("/preparation/fail") and not any(path.endswith("/finish") for path in calls)
