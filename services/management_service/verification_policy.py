"""Durable public-read budget. Lease/budget are spent before anonymous HTTP."""
from datetime import datetime, timedelta
from typing import Any, Literal, Self
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from shared.hitl_execution import canonical
from shared.hitl_verification import VerificationResult

MAX_ATTEMPTS = 8
LEASE_SECONDS = 30
DELAYS = (5, 10, 20, 40, 60, 60, 60)


class VerificationAttempt(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    number: int = Field(ge=1, le=MAX_ATTEMPTS)
    lease_id: str = Field(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
    started_at: datetime
    lease_until: datetime
    completed_at: datetime | None = None
    status: Literal["pending", "observed", "lease_expired", "superseded"] = "pending"
    result: VerificationResult | None = None


class VerificationState(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    version: Literal[1] = 1
    started_at: datetime
    deadline_seconds: int = Field(default=300, ge=30, le=3600)
    deadline_at: datetime
    next_attempt_at: datetime | None
    attempts: tuple[VerificationAttempt, ...] = Field(default=(), max_length=MAX_ATTEMPTS)
    status: Literal["pending", "matched", "failed", "superseded"] = "pending"
    stop_reason: Literal["deadline_exceeded", "attempts_exhausted", "needs_input", "network_policy", "rollback_started"] | None = None

    @model_validator(mode="after")
    def journal(self) -> Self:
        if self.deadline_at - self.started_at != timedelta(seconds=self.deadline_seconds):
            raise ValueError("verification_deadline_invalid")
        dates = [self.started_at, self.deadline_at, self.next_attempt_at]
        for number, attempt in enumerate(self.attempts, 1):
            dates.extend([attempt.started_at, attempt.lease_until, attempt.completed_at])
            if (attempt.number != number or attempt.lease_until-attempt.started_at != timedelta(seconds=LEASE_SECONDS)
                    or (attempt.status == "pending") != (attempt.completed_at is None)
                    or (attempt.status == "observed") != (attempt.result is not None)
                    or (attempt.status == "pending" and number != len(self.attempts))):
                raise ValueError("verification_attempt_invalid")
        if any(value is not None and value.utcoffset() != timedelta(0) for value in dates):
            raise ValueError("verification_utc_required")
        return self

    @classmethod
    def initial(cls, now: datetime, deadline_seconds: int = 300) -> Self:
        return cls(started_at=now, deadline_seconds=deadline_seconds, deadline_at=now+timedelta(seconds=deadline_seconds), next_attempt_at=now)

    @classmethod
    def load(cls, raw: Any) -> Self:
        return cls.model_validate_json(canonical(raw))

    def dump(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    def _settle(self, now: datetime, status: Literal["observed", "lease_expired", "superseded"], result: VerificationResult | None = None) -> Self:
        attempt = self.attempts[-1].model_copy(update={"completed_at": now, "status": status, "result": result})
        next_at = min(now+timedelta(seconds=DELAYS[len(self.attempts)-1]), self.deadline_at) if len(self.attempts)<MAX_ATTEMPTS else None
        return self.model_copy(update={"attempts": (*self.attempts[:-1], attempt), "next_attempt_at": next_at})

    def claim(self, now: datetime) -> tuple[Self, str | None]:
        value = self
        if value.status != "pending":
            return value, None
        if value.attempts and value.attempts[-1].status == "pending":
            if now < min(value.attempts[-1].lease_until, value.deadline_at):
                return value, None
            value = value._settle(min(value.attempts[-1].lease_until, now), "lease_expired")
        if now >= value.deadline_at or len(value.attempts) >= MAX_ATTEMPTS:
            return value.model_copy(update={"status": "failed", "next_attempt_at": None,
                "stop_reason": "deadline_exceeded" if now >= value.deadline_at else "attempts_exhausted"}), None
        if value.next_attempt_at is None or now < value.next_attempt_at:
            return value, None
        attempt = VerificationAttempt(number=len(value.attempts)+1, lease_id=str(uuid4()), started_at=now,
                                      lease_until=now+timedelta(seconds=LEASE_SECONDS))
        return value.model_copy(update={"attempts": (*value.attempts, attempt), "next_attempt_at": attempt.lease_until}), attempt.lease_id

    def reported(self, now: datetime, result: VerificationResult) -> Self:
        value = self._settle(now, "observed", result)
        if result.matches:
            return value.model_copy(update={"status": "matched", "next_attempt_at": None})
        if result.error_code in {"baseline_required", "unsupported_predicate", "network_policy"}:
            return value.model_copy(update={"status": "failed", "next_attempt_at": None,
                "stop_reason": "network_policy" if result.error_code == "network_policy" else "needs_input"})
        if len(value.attempts) == MAX_ATTEMPTS or now >= value.deadline_at:
            return value.model_copy(update={"status": "failed", "next_attempt_at": None,
                "stop_reason": "deadline_exceeded" if now >= value.deadline_at else "attempts_exhausted"})
        return value

    def superseded(self, now: datetime) -> Self:
        value = self._settle(now, "superseded") if self.attempts and self.attempts[-1].status == "pending" else self
        return value.model_copy(update={"status": "superseded", "next_attempt_at": None, "stop_reason": "rollback_started"})

    def summary(self) -> dict[str, Any]:
        return {"status": self.status, "attempts": len(self.attempts), "max_attempts": MAX_ATTEMPTS,
                "deadline_at": self.deadline_at.isoformat(), "next_attempt_at": self.next_attempt_at.isoformat() if self.next_attempt_at else None,
                "stop_reason": self.stop_reason,
                "last_result": self.attempts[-1].result.model_dump(mode="json") if self.attempts and self.attempts[-1].result else None}
