"""Retired decision writers must fail before touching business state or CMS."""
import asyncio
from unittest.mock import Mock
from uuid import uuid4

import pytest

from services.management_service.approval_policy import ApprovalError
from services.management_service.hitl_handler import HITLHandler, handle_hitl_rejected_event
from services.management_service.proposal_handoff import ProposalHandoffError
from services.management_service.schemas.hitl import HITLApprovalCreate, HITLDecision


def test_create_hitl_approval_requires_canonical_version_before_database_access():
    db = Mock()
    data = HITLApprovalCreate(task_id=uuid4(), diff_data={"before": {}, "after": {}}, impact_score=0.7)
    with pytest.raises(ApprovalError, match="version_bound_preview_required"):
        HITLHandler(db).create_hitl_approval(str(data.task_id), data)
    assert db.mock_calls == []


def test_approve_task_requires_verified_actor_and_version_before_database_access():
    db = Mock()
    with pytest.raises(ApprovalError, match="verified_user_decision_required"):
        asyncio.run(HITLHandler(db).approve_task(str(uuid4()), "forged-user", HITLDecision(auto_deploy=False)))
    assert db.mock_calls == []


def test_reject_task_requires_verified_actor_and_version_before_database_access():
    db = Mock()
    with pytest.raises(ApprovalError, match="verified_user_decision_required"):
        HITLHandler(db).reject_task(str(uuid4()), "forged-user", HITLDecision(rejection_reason="forged"))
    assert db.mock_calls == []


def test_approval_cannot_implicitly_deploy():
    db = Mock()
    with pytest.raises(ProposalHandoffError, match="version_bound_executor_required"):
        asyncio.run(HITLHandler(db).approve_task(str(uuid4()), "forged-user", HITLDecision(auto_deploy=True)))
    assert db.mock_calls == []


def test_rejection_event_cannot_impersonate_human_or_rewrite_inflight_task():
    db = Mock()
    with pytest.raises(ApprovalError, match="decision_notification_cannot_make_human_decision"):
        handle_hitl_rejected_event(db, {"task_id": str(uuid4()), "rejected_by": "forged"})
    assert db.mock_calls == []