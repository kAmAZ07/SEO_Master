"""Append snapshots and event envelopes in the caller's transaction; never publish here."""

from copy import deepcopy
from datetime import datetime
import hashlib
import json
from typing import Any, Literal
import uuid

from sqlalchemy import insert, select
from sqlalchemy.orm import Session

from services.management_service.approval_policy import ApprovalError, DecisionRecord
from services.management_service.models import HITLHistory, ManagementOutbox
from services.management_service.proposal_handoff import version_diff
from services.semantic_service.proposals.model import RecommendationVersion

EntryType = Literal["prepared", "approve", "reject", "revoke"]
EVENTS = {
    "prepared": ("HITLApprovalRequired", "management.hitl.approval_required"),
    "approve": ("HITLApproved", "hitl.approved"),
    "reject": ("HITLRejected", "management.hitl.rejected"),
    "revoke": ("HITLRevoked", "management.hitl.revoked"),
}


def snapshot_hash(metadata: dict[str, Any]) -> str:
    body = json.dumps(metadata, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(("SEO-MASTER-HITL-HISTORY-V1\n" + body).encode()).hexdigest()


def append_history(db: Session, *, approval_id: str, task_id: str, version: RecommendationVersion,
                   entry_type: EntryType, metadata: dict[str, Any], now: datetime,
                   emit: bool = True) -> str:
    history_id = uuid.uuid5(uuid.UUID(approval_id), f"{version.version_id}:{entry_type}")
    event_id = uuid.uuid5(history_id, "event")
    snapshot: dict[str, Any] = {"preview": version_diff(version)}
    if entry_type != "prepared":
        snapshot.update({key: deepcopy(metadata.get(key)) for key in (
            "decision_record", "decision_hash", "decision_comment",
        )})
    if entry_type == "revoke":
        snapshot["revocation"] = deepcopy(metadata["revocation"])
    digest = snapshot_hash(snapshot)
    existing = db.execute(select(HITLHistory.entry_hash).where(HITLHistory.id == history_id)).scalar_one_or_none()
    if existing is not None and existing != digest:
        raise ApprovalError("approval_history_conflict")
    if existing is None:
        db.execute(insert(HITLHistory).values(
            id=history_id, approval_id=uuid.UUID(approval_id), task_id=uuid.UUID(task_id),
            project_id=uuid.UUID(version.project_id), version_id=version.version_id,
            entry_type=entry_type, content_hash=version.content_hash, entry_hash=digest,
            meta=snapshot, created_at=now,
        ))
    if not emit:
        return str(history_id)
    if db.execute(select(ManagementOutbox.id).where(ManagementOutbox.id == event_id)).scalar_one_or_none() is None:
        event_type, routing_key = EVENTS[entry_type]
        record = metadata.get("decision_record") or {}
        payload = {
            "task_id": task_id, "project_id": version.project_id, "decision_id": approval_id,
            "history_id": str(history_id), "recommendation_version_id": version.version_id,
            "content_hash": version.content_hash, "url": version.target.url,
            "approval_hash": metadata.get("decision_hash"), "actor_id": record.get("actor_id"),
            "actor_role": record.get("actor_role"), "approved_by": record.get("actor_id") if entry_type == "approve" else None,
            "approved_at": record.get("decided_at") if entry_type == "approve" else None,
            "expires_at": record.get("expires_at"), "auto_deployed": False,
            "publication_status": "not_requested",
        }
        envelope = {"event_id": str(event_id), "event_type": event_type, "event_name": event_type,
                    "schema_version": "hitl_decision_event_v1", "produced_at": now.isoformat(), "payload": payload}
        db.execute(insert(ManagementOutbox).values(
            id=event_id, history_id=history_id, event_type=event_type, routing_key=routing_key,
            status="pending", attempts=0, meta=envelope, available_at=now, created_at=now, updated_at=now,
        ))
    return str(history_id)


def read_approved_notification(db: Session, event: dict[str, Any]) -> dict[str, Any]:
    """A notification reports a persisted human decision; it cannot create one."""
    if event.get("schema_version") != "hitl_decision_event_v1":
        raise ApprovalError("human_decision_required")
    payload = event.get("payload")
    if (not isinstance(payload, dict) or event.get("event_type") != "HITLApproved"
            or event.get("event_name") not in {None, "HITLApproved"}):
        raise ApprovalError("approval_notification_invalid")
    try:
        history_id = uuid.UUID(payload.get("history_id", ""))
    except (ValueError, TypeError, AttributeError):
        raise ApprovalError("human_decision_required") from None
    row = db.execute(select(HITLHistory.entry_type, HITLHistory.entry_hash, HITLHistory.meta).where(
        HITLHistory.id == history_id,
    )).first()
    if row is None or row[0] != "approve" or snapshot_hash(row[2]) != row[1]:
        raise ApprovalError("human_decision_required")
    snapshot = row[2]
    try:
        record = DecisionRecord.model_validate(snapshot.get("decision_record"))
    except ValueError:
        raise ApprovalError("approval_notification_invalid") from None
    expected = {"task_id": record.task_id, "project_id": record.project_id,
                "recommendation_version_id": record.recommendation_version_id,
                "content_hash": record.recommendation_content_hash, "approval_hash": record.digest(),
                "actor_id": record.actor_id, "approved_by": record.actor_id, "actor_role": record.actor_role.value}
    if (record.decision != "approve" or record.digest() != snapshot.get("decision_hash")
            or any(payload.get(key) != value for key, value in expected.items())
            or payload.get("auto_deployed") is not False):
        raise ApprovalError("approval_notification_invalid")
    return {"status": "notification_recorded", "task_id": record.task_id,
            "approval_hash": record.digest(), "publication_status": "not_requested"}
