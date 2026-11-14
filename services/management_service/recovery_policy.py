"""Persisted, bounded status-only recovery. No HTTP or settings side effects."""
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Self
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from shared.hitl_execution import RecoveryError, canonical

DELAYS = (10, 60, 300)
MAX_ATTEMPTS = 4
LEASE_SECONDS = 60
DEADLINE_SECONDS = 900


class Attempt(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    number: int = Field(ge=1, le=MAX_ATTEMPTS)
    lease_id: str = Field(pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
    started_at: datetime
    lease_until: datetime
    status: Literal["pending", "failed", "lease_expired", "succeeded", "superseded", "abandoned"] = "pending"
    completed_at: datetime | None = None
    error_code: RecoveryError | Literal["lease_expired"] | None = None

    @field_validator("started_at", "lease_until", "completed_at")
    @classmethod
    def utc(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.utcoffset() != timedelta(0):
            raise ValueError("recovery_utc_required")
        return value


class RecoveryState(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    version: Literal[1] = 1
    started_at: datetime
    deadline_at: datetime
    next_attempt_at: datetime | None
    attempts: tuple[Attempt, ...] = Field(default=(), max_length=MAX_ATTEMPTS)
    last_error: RecoveryError | Literal["lease_expired"] | None = None
    stop_reason: Literal["attempts_exhausted", "deadline_exceeded"] | None = None

    @field_validator("started_at", "deadline_at", "next_attempt_at")
    @classmethod
    def utc(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.utcoffset() != timedelta(0):
            raise ValueError("recovery_utc_required")
        return value

    @model_validator(mode="after")
    def journal(self) -> Self:
        if self.deadline_at - self.started_at != timedelta(seconds=DEADLINE_SECONDS):
            raise ValueError("recovery_deadline_invalid")
        for number, attempt in enumerate(self.attempts, 1):
            if (attempt.number != number or attempt.started_at.utcoffset() != timedelta(0)
                    or attempt.lease_until - attempt.started_at != timedelta(seconds=LEASE_SECONDS)
                    or (attempt.status == "pending") != (attempt.completed_at is None)
                    or (attempt.status == "pending" and number != len(self.attempts))):
                raise ValueError("recovery_attempt_invalid")
            if (attempt.completed_at is not None and attempt.completed_at < attempt.started_at
                    or (attempt.status == "failed" and attempt.error_code is None)
                    or (attempt.status == "lease_expired" and attempt.error_code != "lease_expired")
                    or (attempt.status in {"pending", "succeeded", "superseded", "abandoned"} and attempt.error_code is not None)):
                raise ValueError("recovery_attempt_invalid")
        if len({item.lease_id for item in self.attempts}) != len(self.attempts):
            raise ValueError("recovery_attempt_invalid")
        return self

    @classmethod
    def initial(cls, now: datetime, command_deadline: int) -> Self:
        return cls(started_at=now, deadline_at=now + timedelta(seconds=DEADLINE_SECONDS),
                   next_attempt_at=min(now + timedelta(seconds=DEADLINE_SECONDS),
                       datetime.fromtimestamp(command_deadline + 10, timezone.utc)))

    @classmethod
    def load(cls, raw: Any) -> Self:
        return cls.model_validate_json(canonical(raw))

    def dump(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    def settled(self, now: datetime, status: Literal["failed", "lease_expired", "succeeded", "superseded"],
                error: RecoveryError | Literal["lease_expired"] | None = None) -> Self:
        last = self.attempts[-1]
        attempt = last.model_copy(update={"status": status, "completed_at": now, "error_code": error})
        next_at = min(now + timedelta(seconds=DELAYS[len(self.attempts) - 1]), self.deadline_at) if len(self.attempts) < MAX_ATTEMPTS else None
        return self.model_copy(update={"attempts": (*self.attempts[:-1], attempt), "last_error": error,
                                       "next_attempt_at": next_at})

    def claim(self, now: datetime) -> tuple[Self, str | None]:
        value = self
        if value.stop_reason is not None:
            return value, None
        if value.attempts and value.attempts[-1].status == "pending":
            last = value.attempts[-1]
            if now >= value.deadline_at:
                abandoned = last.model_copy(update={"status": "abandoned", "completed_at": now})
                return value.model_copy(update={"attempts": (*value.attempts[:-1], abandoned),
                    "next_attempt_at": None, "stop_reason": "deadline_exceeded"}), None
            if now < last.lease_until:
                return value, None
            value = value.settled(last.lease_until, "lease_expired", "lease_expired")
        if now >= value.deadline_at or len(value.attempts) >= MAX_ATTEMPTS:
            reason = "deadline_exceeded" if now >= value.deadline_at else "attempts_exhausted"
            return value.model_copy(update={"next_attempt_at": None, "stop_reason": reason}), None
        if value.next_attempt_at is None or now < value.next_attempt_at:
            return value, None
        attempt = Attempt(number=len(value.attempts) + 1, lease_id=str(uuid4()), started_at=now,
                          lease_until=now + timedelta(seconds=LEASE_SECONDS))
        return value.model_copy(update={"attempts": (*value.attempts, attempt), "next_attempt_at": attempt.lease_until}), attempt.lease_id

    def reported(self, now: datetime, error: RecoveryError | None) -> Self:
        value = self.settled(now, "failed" if error else "succeeded", error)
        if error and (len(value.attempts) >= MAX_ATTEMPTS or now >= value.deadline_at):
            reason = "deadline_exceeded" if now >= value.deadline_at else "attempts_exhausted"
            value = value.model_copy(update={"stop_reason": reason, "next_attempt_at": None})
        return value

    def summary(self) -> dict[str, Any]:
        return {"attempts": len(self.attempts), "max_attempts": MAX_ATTEMPTS,
                "next_attempt_at": self.next_attempt_at.isoformat() if self.next_attempt_at else None,
                "deadline_at": self.deadline_at.isoformat(), "last_error": self.last_error, "stop_reason": self.stop_reason}
