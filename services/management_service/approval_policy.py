"""Version-bound human decisions. No HTTP, ORM, configuration or CMS imports."""

from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from services.semantic_service.proposals.model import RecommendationVersion, UUID_RE
from shared.projects.roles import Role


class ApprovalError(ValueError):
    def __init__(self, code: str, http_status: int = 409):
        self.code = code
        self.http_status = http_status
        super().__init__(code)


@dataclass(frozen=True)
class UserActor:
    user_id: str


class DecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    approval_id: str = Field(pattern=UUID_RE.pattern)
    recommendation_version_id: str = Field(pattern=UUID_RE.pattern)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    comment: str | None = Field(default=None, max_length=1000)
    auto_deploy: bool = False


class DecisionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal["hitl_decision_v1"] = "hitl_decision_v1"
    approval_id: str
    task_id: str
    project_id: str
    workspace_id: str
    recommendation_version_id: str
    recommendation_content_hash: str
    target_url: str
    actor_id: str
    actor_role: Role
    decision: Literal["approve", "reject"]
    decided_at: datetime
    expires_at: datetime

    @field_validator("decided_at", "expires_at")
    @classmethod
    def aware_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("timezone required")
        return value.astimezone(timezone.utc)

    def digest(self) -> str:
        payload = json.dumps(self.model_dump(mode="json"), ensure_ascii=False,
                             sort_keys=True, separators=(",", ":"), allow_nan=False)
        return hashlib.sha256(("SEO-MASTER-HITL-DECISION-V1\n" + payload).encode()).hexdigest()


def make_record(*, approval_id: str, task_id: str, workspace_id: str,
                version: RecommendationVersion, actor_id: str, actor_role: str,
                decision: Literal["approve", "reject"], now: datetime,
                ttl: timedelta) -> DecisionRecord:
    if now.tzinfo is None or ttl <= timedelta(0):
        raise ApprovalError("approval_clock_invalid")
    return DecisionRecord(
        approval_id=approval_id, task_id=task_id, project_id=version.project_id,
        workspace_id=workspace_id, recommendation_version_id=version.version_id,
        recommendation_content_hash=version.content_hash, target_url=version.target.url,
        actor_id=actor_id, actor_role=Role(actor_role), decision=decision,
        decided_at=now, expires_at=now + ttl,
    )


def record_state(metadata: dict[str, Any], *, version: RecommendationVersion, now: datetime) -> str:
    raw = metadata.get("decision_record")
    if raw is None:
        return "pending"
    try:
        record = DecisionRecord.model_validate(raw)
    except ValueError:
        return "invalidated"
    if metadata.get("decision_hash") != record.digest():
        return "invalidated"
    if (record.recommendation_version_id != version.version_id
            or record.recommendation_content_hash != version.content_hash
            or record.target_url != version.target.url or record.project_id != version.project_id
            or record.task_id != version.task_id):
        return "invalidated"
    if metadata.get("revocation") is not None:
        return "revoked"
    if record.decision == "reject":
        return "rejected"
    if not record.decided_at <= now < record.expires_at:
        return "expired"
    return "approved"
