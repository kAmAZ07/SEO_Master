import importlib
import sys
import types
import enum
import uuid

import pytest


def _install_orchestrator_stubs():
    if "app" in sys.modules:
        return

    app = types.ModuleType("app")
    core = types.ModuleType("app.core")
    config = types.ModuleType("app.core.config")
    logging_mod = types.ModuleType("app.core.logging")
    events = types.ModuleType("app.events")
    publishers = types.ModuleType("app.events.publishers")
    db = types.ModuleType("app.db")
    models = types.ModuleType("app.db.models")

    class Settings:
        SERVICE_REQUEST_TIMEOUT = 5
        INTERNAL_API_KEY = "test-key"
        AUDIT_SERVICE_URL = "http://audit"
        SEMANTIC_SERVICE_URL = "http://semantic"
        CLIENT_GATEWAY_URL = "http://client"
        HITL_TIMEOUT_HOURS = 1
        SAGA_TIMEOUT_MINUTES = 1
        SAGA_RETRY_MAX_ATTEMPTS = 1

    class DummyLogger:
        def info(self, *args, **kwargs):
            pass
        def error(self, *args, **kwargs):
            pass

    class TaskStatus(enum.Enum):
        PENDING = "PENDING"
        COMPLETED = "COMPLETED"
        CANCELLED = "CANCELLED"
        FAILED = "FAILED"

    class HITLStatus(enum.Enum):
        PENDING = "PENDING"
        APPROVED = "APPROVED"
        REJECTED = "REJECTED"

    class HITLDecision:
        def __init__(self, **kwargs):
            self.id = str(uuid.uuid4())
            for key, value in kwargs.items():
                setattr(self, key, value)

    class SagaExecution:
        def __init__(self, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)

    class Task:
        pass

    class Project:
        pass

    async def publish_event(*args, **kwargs):
        return None

    config.settings = Settings()
    logging_mod.logger = DummyLogger()

    models.Project = Project
    models.Task = Task
    models.TaskStatus = TaskStatus
    models.HITLDecision = HITLDecision
    models.HITLStatus = HITLStatus
    models.SagaExecution = SagaExecution

    publishers.publish_event = publish_event

    sys.modules["app"] = app
    sys.modules["app.core"] = core
    sys.modules["app.core.config"] = config
    sys.modules["app.core.logging"] = logging_mod
    sys.modules["app.events"] = events
    sys.modules["app.events.publishers"] = publishers
    sys.modules["app.db"] = db
    sys.modules["app.db.models"] = models


def _import_orchestrator():
    _install_orchestrator_stubs()
    if "services.management_service.orchestrator" in sys.modules:
        return importlib.reload(sys.modules["services.management_service.orchestrator"])
    return importlib.import_module("services.management_service.orchestrator")


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


@pytest.mark.asyncio
async def test_create_hitl_decision_builds_event(monkeypatch):
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
