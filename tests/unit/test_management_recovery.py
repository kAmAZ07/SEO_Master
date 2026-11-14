"""Bounded durable recovery policy: clocks, crash leases and immutable prefix."""
from datetime import datetime, timedelta, timezone

import pytest

from services.management_service.recovery_policy import RecoveryState

NOW = datetime(2026, 11, 14, tzinfo=timezone.utc)


def test_backoff_and_budget_survive_serialization_and_a_new_service_instance():
    value = RecoveryState.initial(NOW, int(NOW.timestamp()))
    value = value.model_copy(update={"next_attempt_at": NOW})
    current = NOW
    for number, delay in enumerate((10, 60, 300, None), 1):
        value, token = RecoveryState.load(value.dump()).claim(current)
        assert token is not None and len(value.attempts) == number
        value = value.reported(current, "cms_unavailable")
        if delay is not None:
            assert value.next_attempt_at == current + timedelta(seconds=delay)
            blocked, token = value.claim(current + timedelta(seconds=delay - 1))
            assert token is None and blocked == value
            current += timedelta(seconds=delay)
    assert value.stop_reason == "attempts_exhausted" and value.next_attempt_at is None
    assert RecoveryState.load(value.dump()).claim(current + timedelta(hours=1)) == (value, None)


def test_expired_lease_consumes_attempt_and_preserves_backoff_from_expiry():
    value = RecoveryState.initial(NOW, int(NOW.timestamp()))
    value = value.model_copy(update={"next_attempt_at": NOW})
    leased, token = value.claim(NOW)
    assert token is not None
    assert RecoveryState.load(leased.dump()).claim(NOW + timedelta(seconds=59)) == (leased, None)
    expired, token = RecoveryState.load(leased.dump()).claim(NOW + timedelta(seconds=60))
    assert token is None and len(expired.attempts) == 1
    assert expired.attempts[0].status == "lease_expired"
    next_value, token = expired.claim(NOW + timedelta(seconds=70))
    assert token is not None and len(next_value.attempts) == 2
    assert next_value.attempts[0] == expired.attempts[0]


def test_overall_deadline_does_not_start_another_status_request():
    value = RecoveryState.initial(NOW, int(NOW.timestamp()))
    exhausted, token = value.claim(NOW + timedelta(seconds=900))
    assert token is None and exhausted.stop_reason == "deadline_exceeded"
    assert exhausted.attempts == ()


def test_corrupt_or_naive_recovery_metadata_is_not_accepted():
    value = RecoveryState.initial(NOW, int(NOW.timestamp())).dump()
    value["started_at"] = "2026-11-14T00:00:00"
    with pytest.raises(ValueError):
        RecoveryState.load(value)


def test_service_recovery_requires_both_keys_and_does_not_grant_actorless_begin(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from unittest.mock import Mock
    from services.management_service.api.endpoints import executions
    from services.management_service.db.session import get_db
    from services.management_service.config import settings
    monkeypatch.setattr(settings, "INTERNAL_API_KEY", "i" * 32)
    monkeypatch.setattr(settings, "EXECUTOR_API_KEY", "e" * 32)
    service = Mock()
    service.return_value.claim.return_value = {"operation": None, "lease_id": None}
    monkeypatch.setattr(executions, "ExecutionRecovery", service)
    app = FastAPI()
    app.include_router(executions.router, prefix="/internal/executions")
    app.dependency_overrides[get_db] = lambda: Mock()
    client = TestClient(app)
    for headers in ({}, {"X-Internal-API-Key": "i" * 32}, {"X-Executor-Key": "e" * 32}):
        assert client.post("/internal/executions/recovery/claim", json={}, headers=headers).status_code == 401
    service.assert_not_called()
    headers = {"X-Internal-API-Key": "i" * 32, "X-Executor-Key": "e" * 32}
    assert client.post("/internal/executions/recovery/claim", json={}, headers=headers).status_code == 200
    assert service.return_value.claim.call_count == 1
    assert client.post("/internal/executions/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa/begin", headers=headers).status_code == 401
