"""DEC-04 estimates and stable ordering; no ORM, configuration or network."""
from datetime import datetime
from typing import Any, Literal, Mapping, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Urgency = Literal['critical', 'high', 'medium', 'low', 'unknown']
URGENCY_RANK: dict[Urgency, int] = {'critical': 0, 'high': 1, 'medium': 2, 'low': 3, 'unknown': 4}


def reason_text(value: str) -> str:
    value = value.strip()
    if not value or '\x00' in value or len(value.encode('utf-8')) > 1024:
        raise ValueError('priority_reason_required_or_invalid')
    return value


class PriorityInputs(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True, allow_inf_nan=False)
    impact: float | None = Field(default=None, ge=0, le=1)
    confidence: float | None = Field(default=None, ge=0, le=1)
    effort: float | None = Field(default=None, ge=1, le=5)
    urgency: Urgency = 'unknown'
    urgency_reason: str | None = None

    @field_validator('urgency_reason')
    @classmethod
    def valid_reason(cls, value: str | None) -> str | None:
        return reason_text(value) if value is not None else None

    @model_validator(mode='after')
    def urgency_explained(self) -> 'PriorityInputs':
        if self.urgency != 'unknown' and self.urgency_reason is None:
            raise ValueError('priority_urgency_reason_required')
        return self


def calculate_priority(impact: float | None, confidence: float | None, effort: float | None) -> float | None:
    inputs = PriorityInputs(impact=impact, confidence=confidence, effort=effort)
    if inputs.impact is None or inputs.confidence is None or inputs.effort is None:
        return None
    return inputs.impact * inputs.confidence / inputs.effort


class PriorityOverride(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True, allow_inf_nan=False)
    score: float = Field(ge=0, le=1)
    actor_id: str
    reason: str
    at: str


class PriorityRecord(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    formula_version: Literal['priority_v1'] = 'priority_v1'
    revision: int = Field(default=0, ge=0)
    inputs: PriorityInputs = Field(default_factory=PriorityInputs)
    override: PriorityOverride | None = None

    def snapshot(self) -> dict[str, Any]:
        computed = calculate_priority(self.inputs.impact, self.inputs.confidence, self.inputs.effort)
        return {**self.model_dump(mode='json'), 'computed_score': computed,
                'effective_score': self.override.score if self.override is not None else computed,
                'status': 'manual_override' if self.override is not None else 'unknown' if computed is None else 'estimated',
                'urgency_rank': URGENCY_RANK[self.inputs.urgency], 'heuristic': True}


class PriorityChange(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True, allow_inf_nan=False)
    action: Literal['estimate', 'override', 'clear_override']
    expected_revision: int = Field(ge=0)
    inputs: PriorityInputs | None = None
    score: float | None = Field(default=None, ge=0, le=1)
    reason: str

    @field_validator('reason')
    @classmethod
    def valid_reason(cls, value: str) -> str:
        return reason_text(value)

    @model_validator(mode='after')
    def exact_command(self) -> 'PriorityChange':
        if ((self.action == 'estimate') != (self.inputs is not None)
                or (self.action == 'override') != (self.score is not None)):
            raise ValueError('priority_command_fields_invalid')
        return self


def load_priority(metadata: Mapping[str, Any] | None) -> PriorityRecord:
    if metadata is None or 'priority' not in metadata:
        return PriorityRecord()
    return PriorityRecord.model_validate(metadata['priority'])


class QueueTask(Protocol):
    id: UUID
    created_at: datetime
    meta: dict[str, Any] | None


class PriorityTask(QueueTask, Protocol):
    project_id: UUID
    priority_score: float | None
    updated_at: datetime

    def calculate_priority(self) -> float | None: ...


def priority_key(task: QueueTask) -> tuple[int, bool, float, datetime, str]:
    record = load_priority(task.meta)
    score = calculate_priority(record.inputs.impact, record.inputs.confidence, record.inputs.effort)
    if record.override is not None:
        score = record.override.score
    return URGENCY_RANK[record.inputs.urgency], score is None, -(score or 0.), task.created_at, str(task.id)
