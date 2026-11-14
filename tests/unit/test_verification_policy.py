"""Retry limits survive serialization and stale/failed public reads never verify."""
from datetime import datetime, timedelta, timezone

from services.management_service.verification_policy import MAX_ATTEMPTS, VerificationState
from shared.hitl_verification import VerificationResult

NOW = datetime(2026, 11, 14, tzinfo=timezone.utc)


def test_restart_keeps_deadline_and_spent_lease():
    original = VerificationState.initial(NOW)
    claimed, token = original.claim(NOW)
    loaded = VerificationState.load(claimed.dump())
    assert loaded.claim(NOW + timedelta(seconds=29)) == (loaded, None)
    expired, next_token = loaded.claim(NOW + timedelta(seconds=30))
    assert next_token is None and expired.attempts[0].status == "lease_expired"
    assert expired.deadline_at == original.deadline_at
    assert token == expired.attempts[0].lease_id
    next_value, next_token = expired.claim(NOW + timedelta(seconds=35))
    assert next_token and next_token != token and len(next_value.attempts) == 2


def test_mismatch_budget_is_finite_and_cannot_restart():
    value = VerificationState.initial(NOW, 3600)
    for number in range(MAX_ATTEMPTS):
        now = value.next_attempt_at
        value, token = value.claim(now)
        assert token and len(value.attempts) == number + 1
        value = VerificationState.load(value.reported(now, VerificationResult(error_code="predicate_mismatch")).dump())
    assert value.status == "failed" and value.stop_reason == "attempts_exhausted"
    assert value.claim(NOW + timedelta(hours=1)) == (value, None)


def test_deadline_overrides_active_lease_and_rollback_spends_pending_token():
    value, _ = VerificationState.initial(NOW, 30).claim(NOW + timedelta(seconds=29))
    failed, token = value.claim(NOW + timedelta(seconds=30))
    assert token is None and failed.status == "failed" and failed.stop_reason == "deadline_exceeded"
    superseded = value.superseded(NOW + timedelta(seconds=29))
    assert superseded.status == "superseded" and superseded.attempts[-1].status == "superseded"
    assert superseded.claim(NOW + timedelta(seconds=30)) == (superseded, None)
