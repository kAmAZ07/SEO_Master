"""Bounded read-only preparation; its lease never authorizes a CMS mutation."""
from datetime import datetime, timedelta
from typing import Any, Literal, Self
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from shared.hitl_execution import PreparationError, PreparationPhase, canonical

MAX_ATTEMPTS = 4
LEASE_SECONDS = 60
DEADLINE_SECONDS = 900
OPEN_PHASES = frozenset({"queued", "preflight", "dry_run", "ready"})
FAILED_PHASES = frozenset({"rejected", "unsupported", "conflict", "failed_before_apply"})


def failure_phase(error: PreparationError) -> Literal["rejected", "unsupported", "conflict", "failed_before_apply"]:
    if error == "authorization_rejected":
        return "rejected"
    if error == "cms_precondition_conflict":
        return "conflict"
    return "unsupported" if error == "cms_unsupported" else "failed_before_apply"


class PreparationEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    phase: PreparationPhase
    at: datetime
    attempt: int = Field(ge=0, le=MAX_ATTEMPTS)
    error_code: PreparationError | None = None


class PreparationState(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    version: Literal[1] = 1
    started_at: datetime
    deadline_at: datetime
    attempts: int = Field(default=0, ge=0, le=MAX_ATTEMPTS)
    lease_id: str | None = Field(default=None, pattern=r"^[0-9a-f-]{36}$")
    lease_until: datetime | None = None
    events: tuple[PreparationEvent, ...] = Field(min_length=1, max_length=20)

    @field_validator("started_at", "deadline_at", "lease_until")
    @classmethod
    def utc(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.utcoffset() != timedelta(0):
            raise ValueError("preparation_utc_required")
        return value

    @model_validator(mode="after")
    def journal(self) -> Self:
        if (self.deadline_at - self.started_at != timedelta(seconds=DEADLINE_SECONDS)
                or (self.lease_id is None) != (self.lease_until is None)
                or (self.attempts == 0) != (self.lease_id is None)):
            raise ValueError("preparation_journal_invalid")
        previous = self.started_at
        for event in self.events:
            if (event.at.utcoffset() != timedelta(0) or event.at < previous or event.attempt > self.attempts
                    or (event.phase in FAILED_PHASES) != (event.error_code is not None)):
                raise ValueError("preparation_journal_invalid")
            previous = event.at
        return self

    @classmethod
    def initial(cls, now: datetime, phase: PreparationPhase = "queued") -> Self:
        return cls(started_at=now, deadline_at=now + timedelta(seconds=DEADLINE_SECONDS),
                   events=(PreparationEvent(phase=phase, at=now, attempt=0),))

    @classmethod
    def load(cls, raw: Any) -> Self:
        return cls.model_validate_json(canonical(raw))

    def dump(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    def live(self, token: str | None, now: datetime) -> bool:
        return bool(token and token == self.lease_id and self.lease_until and now < self.lease_until and now < self.deadline_at)

    def claimed(self, now: datetime) -> Self:
        value = self.model_copy(update={"attempts": self.attempts + 1, "lease_id": str(uuid4()),
            "lease_until": min(now + timedelta(seconds=LEASE_SECONDS), self.deadline_at)})
        return value.transition("preflight", now, force=True)

    def transition(self, phase: PreparationPhase, now: datetime, error: PreparationError | None = None, *, force: bool = False) -> Self:
        if not force and self.events[-1].phase == phase:
            return self
        event = PreparationEvent(phase=phase, at=now, attempt=self.attempts, error_code=error)
        return self.model_copy(update={"events": (*self.events, event)})

    def summary(self) -> dict[str, Any]:
        return {"attempts": self.attempts, "max_attempts": MAX_ATTEMPTS,
                "deadline_at": self.deadline_at.isoformat(), "lease_until": self.lease_until.isoformat() if self.lease_until else None,
                "error_code": self.events[-1].error_code,
                "events": [event.model_dump(mode="json") for event in self.events]}
