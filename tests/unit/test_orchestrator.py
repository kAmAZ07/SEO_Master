import asyncio
import importlib
import os
import sys

import pytest

pytest.importorskip("sqlalchemy")
pytest.importorskip("httpx")
pytest.importorskip("tenacity")


def _import_orchestrator():
    os.environ.setdefault("DATABASE_URL", "postgresql://seo_user:seo_pass@localhost:5432/seo_platform")
    os.environ.setdefault("REDIS_URL", "redis://:redis_pass@localhost:6379/0")
    os.environ.setdefault("INTERNAL_API_KEY", "x" * 32)

    module_name = "services.management_service.orchestrator"
    if module_name in sys.modules:
        return importlib.reload(sys.modules[module_name])
    return importlib.import_module(module_name)


class DbStub:
    def __init__(self):
        self.added = []
        self.committed = False
        self.refreshed = []

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        self.committed = True

    def refresh(self, obj):
        self.refreshed.append(obj)


def test_create_hitl_decision_builds_event(monkeypatch):
    async def _run():
        orchestrator = _import_orchestrator()

        events = []

        async def fake_publish_event(**kwargs):
            events.append(kwargs)

        monkeypatch.setattr(orchestrator, "publish_event", fake_publish_event)

        saga = orchestrator.OptimizationSaga(
            project_id="proj-1",
            url="https://example.com",
            task_id="task-1",
        )
        saga.context = {
            "crawl_result": {
                "title": "Old Title",
                "description": "Old Desc",
                "h1": "Old H1",
                "schema_org": {"type": "WebPage"},
            },
            "generated_content": {
                "title": "New Title",
                "description": "New Desc",
                "h1": "New H1",
                "schema_org": {"type": "WebPage"},
            },
            "ffscore": 60,
            "eeat_score": 70,
        }

        db = DbStub()
        decision = await saga._create_hitl_decision(db)

        assert decision.status == orchestrator.HITLStatus.PENDING
        assert decision.old_content["title"] == "Old Title"
        assert decision.new_content["title"] == "New Title"
        assert db.committed is True
        assert events
        assert events[0]["event_type"] == "HITLApprovalRequired"
        assert events[0]["routing_key"] == "management.hitl.approval_required"

    asyncio.run(_run())
