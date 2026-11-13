"""Frozen execution references and WordPress v2 wire rules, shared across locks.

Dynamic JSON is validated at this boundary; no runtime settings or database imports.
"""

from copy import deepcopy
from datetime import datetime
import hashlib
import hmac
import json
import re
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

UUID_PATTERN = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
HASH_PATTERN = r"^[0-9a-f]{64}$"
RecoveryError = Literal["cms_unavailable", "operation_not_found", "receipt_invalid", "integration_changed"]


class RecoveryClaim(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    operation_id: str | None = Field(default=None, pattern=UUID_PATTERN)


class RecoveryReport(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    lease_id: str = Field(pattern=UUID_PATTERN)
    receipt: dict[str, Any] | None = None
    error_code: RecoveryError | None = None

    @model_validator(mode="after")
    def one_result(self) -> Self:
        if (self.receipt is None) == (self.error_code is None):
            raise ValueError("one_recovery_result_required")
        return self


class ExecutionError(ValueError):
    pass


class ExecutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    operation_id: str = Field(pattern=UUID_PATTERN)
    task_id: str = Field(pattern=UUID_PATTERN)
    approval_id: str = Field(pattern=UUID_PATTERN)
    recommendation_version_id: str = Field(pattern=UUID_PATTERN)
    content_hash: str = Field(pattern=HASH_PATTERN)


class WordPressBinding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    base_url: str = Field(min_length=8, max_length=2048)
    site_id: str = Field(pattern=UUID_PATTERN)
    key_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def bind_proposal_hash(body: dict[str, Any]) -> None:
    proposal = {key: value for key, value in body.items() if key not in {"operation_id", "approval_ref", "proposal_hash"}}
    body["proposal_hash"] = hashlib.sha256(("SEO-MASTER-PROPOSAL-V2\n" + canonical(proposal)).encode()).hexdigest()


def snapshot(value: Any) -> dict[str, Any]:
    if (not isinstance(value, dict) or set(value) != {"entity_id", "post_type", "post_status", "fields"}
            or not isinstance(value["entity_id"], int) or isinstance(value["entity_id"], bool) or not 1 <= value["entity_id"] <= 2147483647
            or not isinstance(value["post_type"], str) or value["post_type"] not in {"post", "page"} or value["post_status"] != "publish"
            or not isinstance(value["fields"], dict) or set(value["fields"]) != {"title", "description", "jsonld"}):
        raise ExecutionError("cms_snapshot_invalid")
    for state in value["fields"].values():
        if not isinstance(state, dict):
            raise ExecutionError("cms_snapshot_invalid")
        if state == {"state": "missing"}:
            continue
        if (set(state) != {"state", "value"} or state["state"] != "value" or not isinstance(state["value"], str)
                or len(state["value"].encode("utf-8")) > 8192 or "\x00" in state["value"]):
            raise ExecutionError("cms_snapshot_invalid")
    return deepcopy(value)


def make_command(version: dict[str, Any], references: ExecutionRequest, binding: WordPressBinding,
                 before: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    before = snapshot(before)
    entity = version["target"]["entity"]
    if (version["execution_mode"] != "managed" or not isinstance(entity, dict)
            or entity.get("kind") not in {"post", "page"} or not isinstance(entity.get("id"), str)
            or not entity["id"].isdigit() or entity["id"] != str(before["entity_id"])
            or entity["kind"] != before["post_type"]):
        raise ExecutionError("managed_cms_target_required")
    after = deepcopy(before)
    changes: list[dict[str, Any]] = []
    for change in version["changes"]:
        field = change["cms_field"]
        if field not in {"title", "description", "jsonld"} or any(item["field"] == field for item in changes):
            raise ExecutionError("cms_field_unsupported")
        expected = {"state": "missing"} if change["before_state"] == "missing" else {"state": "value", "value": change["before"]}
        if before["fields"][field] != expected:
            raise ExecutionError("cms_precondition_conflict")
        if change["mode"] == "set":
            changes.append({"op": "set", "field": field, "value": change["after"]})
            after["fields"][field] = {"state": "value", "value": change["after"]}
        elif change["mode"] == "remove":
            changes.append({"op": "remove", "field": field})
            after["fields"][field] = {"state": "missing"}
        elif change["mode"] != "unchanged":
            raise ExecutionError("cms_change_invalid")
    if not changes or len(changes) > 3:
        raise ExecutionError("cms_changes_required")
    snapshot(after)
    body = {"project_id": version["project_id"], "site_id": binding.site_id, "entity_id": before["entity_id"],
            "operation_id": references.operation_id, "task_id": references.task_id,
            "proposal_version": references.recommendation_version_id, "approval_ref": references.approval_id,
            "before_digest": digest(before), "expected_after_digest": digest(after), "changes": changes}
    proposal = {key: value for key, value in body.items() if key not in {"operation_id", "approval_ref"}}
    body["proposal_hash"] = hashlib.sha256(("SEO-MASTER-PROPOSAL-V2\n" + canonical(proposal)).encode()).hexdigest()
    if len(canonical(body).encode()) > 16384:
        raise ExecutionError("cms_command_too_large")
    return body, after


def validate_receipt(receipt: Any, command: dict[str, Any], before: dict[str, Any], after: dict[str, Any],
                     *, secret: str | None = None, key_id: str | None = None) -> dict[str, Any]:
    required = {"protocol", "kind", "receipt_id", "operation_id", "project_id", "site_id", "entity_id", "task_id",
                "proposal_version", "proposal_hash", "approval_ref", "request_hash", "before_digest", "after_digest",
                "before", "after", "plugin_version", "key_id", "applied_at", "applied_fields", "outcome", "warnings", "signature"}
    if not isinstance(receipt, dict) or set(receipt) != required:
        raise ExecutionError("cms_receipt_invalid")
    if (not isinstance(receipt["entity_id"], int) or isinstance(receipt["entity_id"], bool)
            or not isinstance(receipt["plugin_version"], str) or re.fullmatch(r"[A-Za-z0-9._-]{1,32}", receipt["plugin_version"]) is None
            or not isinstance(receipt["key_id"], str) or re.fullmatch(r"[A-Za-z0-9._-]{1,64}", receipt["key_id"]) is None
            or not isinstance(receipt["signature"], str) or re.fullmatch(HASH_PATTERN, receipt["signature"]) is None
            or not isinstance(receipt["applied_at"], str) or re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", receipt["applied_at"]) is None
            or not isinstance(receipt["warnings"], list) or len(receipt["warnings"]) > 16
            or any(not isinstance(item, str) or len(item.encode("utf-8")) > 128 or "\x00" in item for item in receipt["warnings"])):
        raise ExecutionError("cms_receipt_invalid")
    try:
        datetime.strptime(receipt["applied_at"], "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise ExecutionError("cms_receipt_invalid") from exc
    try:
        snapshot(receipt["before"])
        snapshot(receipt["after"])
    except ExecutionError as exc:
        raise ExecutionError("cms_receipt_invalid") from exc
    request_hash = hashlib.sha256(canonical(command).encode()).hexdigest()
    expected = {key: command[key] for key in ("operation_id", "project_id", "site_id", "entity_id", "task_id", "proposal_version", "proposal_hash", "approval_ref", "before_digest")}
    expected.update(protocol="seo-master/v2", kind="receipt", request_hash=request_hash,
                    after_digest=digest(after), before=before, after=after,
                    receipt_id=hashlib.sha256(("SEO-MASTER-RECEIPT-ID-V2\n" + command["operation_id"] + "\n" + request_hash).encode()).hexdigest())
    fields = [item["field"] for item in command["changes"] if before["fields"][item["field"]] != after["fields"][item["field"]]]
    expected.update(applied_fields=fields, outcome="applied_unverified" if fields else "no_op")
    if any(receipt.get(key) != value for key, value in expected.items()):
        raise ExecutionError("cms_receipt_binding_invalid")
    if secret is not None:
        unsigned = {key: value for key, value in receipt.items() if key != "signature"}
        receipt_key = hmac.new(secret.encode(), b"SEO-MASTER-RECEIPT-KEY-V2", hashlib.sha256).digest()
        signature = hmac.new(receipt_key, ("SEO-MASTER-RECEIPT-V2\n" + canonical(unsigned)).encode(), hashlib.sha256).hexdigest()
        if (receipt["key_id"] != key_id or not isinstance(receipt["signature"], str)
                or not hmac.compare_digest(receipt["signature"], signature)):
            raise ExecutionError("cms_receipt_signature_invalid")
    return receipt
