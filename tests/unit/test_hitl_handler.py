import uuid
from unittest.mock import MagicMock

import pytest

from services.management_service.db.models import Task, TaskType, TaskStatus, HITLApproval, HITLStatus
from services.management_service.schemas.hitl import HITLApprovalCreate, HITLDecision
from services.management_service import hitl_handler as hitl_module


class QueryStub:
    def __init__(self, result=None):
        self._result = result

    def filter(self, *args, **kwargs):
        return self

    def order_by(self, *args, **kwargs):
        return self

    def offset(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    def first(self):
        return self._result


class DbStub:
    def __init__(self, task=None, approval=None):
        self._task = task
        self._approval = approval
        self.added = []
        self.committed = False
        self.refreshed = []

    def query(self, model):
        if model is Task:
            return QueryStub(self._task)
        if model is HITLApproval:
            return QueryStub(self._approval)
        return QueryStub(None)

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        self.committed = True

    def refresh(self, obj):
        self.refreshed.append(obj)


def _make_task():
    return Task(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        task_type=TaskType.UPDATE_META,
        status=TaskStatus.PENDING,
        url="https://example.com",
        metadata={},
    )


def test_create_hitl_approval_creates_record():
    task = _make_task()
    db = DbStub(task=task, approval=None)
    handler = hitl_module.HITLHandler(db)

    approval_data = HITLApprovalCreate(
        task_id=task.id,
        diff_data={"before": {}, "after": {}},
        impact_score=0.7,
    )

    approval = handler.create_hitl_approval(
        task_id=str(task.id),
        approval_data=approval_data,
        correlation_id="corr-1",
    )

    assert approval.status == HITLStatus.PENDING
    assert db.committed is True
    assert task.status == TaskStatus.PENDING


@pytest.mark.asyncio
async def test_approve_task_without_autodeploy(monkeypatch):
    task = _make_task()
    approval = HITLApproval(
        task_id=task.id,
        project_id=task.project_id,
        status=HITLStatus.PENDING,
        diff_data={"before": {}, "after": {}},
    )
    db = DbStub(task=task, approval=approval)
    handler = hitl_module.HITLHandler(db)

    async def fake_deploy(*args, **kwargs):
        raise AssertionError("deploy_task_changes should not be called")

    async def fake_publish(*args, **kwargs):
        return None

    monkeypatch.setattr(hitl_module, "deploy_task_changes", fake_deploy)
    monkeypatch.setattr(hitl_module, "publish_hitl_approved_event", fake_publish)

    decision = HITLDecision(auto_deploy=False)
    result = await handler.approve_task(
        task_id=str(task.id),
        approved_by="user-1",
        decision=decision,
        correlation_id="corr-2",
    )

    assert result["status"] == "approved"
    assert task.status == TaskStatus.APPROVED
    assert approval.status == HITLStatus.APPROVED


def test_reject_task_sets_status(monkeypatch):
    task = _make_task()
    approval = HITLApproval(
        task_id=task.id,
        project_id=task.project_id,
        status=HITLStatus.PENDING,
        diff_data={"before": {}, "after": {}},
    )
    db = DbStub(task=task, approval=approval)
    handler = hitl_module.HITLHandler(db)

    decision = HITLDecision(rejection_reason="not ok")
    result = handler.reject_task(
        task_id=str(task.id),
        rejected_by="user-2",
        decision=decision,
        correlation_id="corr-3",
    )

    assert result["status"] == "rejected"
    assert task.status == TaskStatus.REJECTED
    assert approval.status == HITLStatus.REJECTED
