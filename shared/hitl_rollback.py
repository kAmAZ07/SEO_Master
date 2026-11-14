"""Rollback intent wire rules: exact original receipt, never a caller inverse patch."""
import hashlib
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from shared.hitl_execution import ExecutionError, UUID_PATTERN, WordPressBinding, canonical, snapshot


class RollbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    operation_id: str = Field(pattern=UUID_PATTERN)
    reason: str = Field(min_length=1, max_length=1024)

    @field_validator("reason")
    @classmethod
    def reason_required(cls, value: str) -> str:
        if not value.strip() or len(value.encode("utf-8")) > 1024 or "\x00" in value:
            raise ValueError("rollback_reason_required")
        return value


def bind_rollback_hash(command: dict[str, Any]) -> None:
    body = {key: value for key, value in command.items() if key not in {"operation_id", "approval_ref", "proposal_hash"}}
    command["proposal_hash"] = hashlib.sha256(("SEO-MASTER-ROLLBACK-PROPOSAL-V2\n" + canonical(body)).encode()).hexdigest()


def make_rollback_command(intent: dict[str, Any], binding: WordPressBinding, before: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    before = snapshot(before)
    if canonical(before) != canonical(intent["expected_current"]) or binding.model_dump() != intent["original_binding"]:
        raise ExecutionError("cms_precondition_conflict")
    after = snapshot(intent["restore_before"])
    command = {"project_id": intent["project_id"], "site_id": binding.site_id, "entity_id": before["entity_id"],
        "operation_id": intent["operation_id"], "task_id": intent["task_id"], "proposal_version": intent["recommendation_version_id"],
        "approval_ref": intent["approval_id"], "operation_type": "rollback", "reason": intent["reason"],
        "original_operation_id": intent["original_operation_id"], "original_receipt_id": intent["original_receipt_id"],
        "original_request_hash": intent["original_request_hash"], "original_proposal_hash": intent["original_proposal_hash"],
        "before_digest": hashlib.sha256(canonical(before).encode()).hexdigest(),
        "expected_after_digest": hashlib.sha256(canonical(after).encode()).hexdigest()}
    return command, after
