import importlib
import sys
import types
import enum
from datetime import datetime, timedelta

import pytest


def _install_prioritizer_stubs():
    if "app" in sys.modules:
        return

    app = types.ModuleType("app")
    core = types.ModuleType("app.core")
    config = types.ModuleType("app.core.config")
    logging_mod = types.ModuleType("app.core.logging")
    db = types.ModuleType("app.db")
    models = types.ModuleType("app.db.models")

    class Settings:
        TASK_PRIORITY_IMPACT_WEIGHT = 0.6
        TASK_PRIORITY_URGENCY_WEIGHT = 0.3
        TASK_PRIORITY_EFFORT_WEIGHT = 0.1
        HITL_AUTO_APPROVE_LOW_RISK = True

    class DummyLogger:
        def info(self, *args, **kwargs):
            pass
        def error(self, *args, **kwargs):
            pass

    class TaskType(enum.Enum):
        UPDATE_META_TAGS = "UPDATE_META_TAGS"
        UPDATE_SCHEMA_ORG = "UPDATE_SCHEMA_ORG"
        UPDATE_H1 = "UPDATE_H1"
        OPTIMIZE_IMAGES = "OPTIMIZE_IMAGES"
        FIX_BROKEN_LINKS = "FIX_BROKEN_LINKS"
        IMPROVE_PAGE_SPEED = "IMPROVE_PAGE_SPEED"
        REWRITE_CONTENT = "REWRITE_CONTENT"
        ADD_INTERNAL_LINKS = "ADD_INTERNAL_LINKS"
        UPDATE_CANONICAL = "UPDATE_CANONICAL"
        FIX_DUPLICATE_CONTENT = "FIX_DUPLICATE_CONTENT"

    class TaskStatus(enum.Enum):
        PENDING = "PENDING"
        QUEUED = "QUEUED"

    class Task:
        pass

    config.settings = Settings()
    logging_mod.logger = DummyLogger()

    models.TaskType = TaskType
    models.TaskStatus = TaskStatus
    models.Task = Task

    sys.modules["app"] = app
    sys.modules["app.core"] = core
    sys.modules["app.core.config"] = config
    sys.modules["app.core.logging"] = logging_mod
    sys.modules["app.db"] = db
    sys.modules["app.db.models"] = models


def _import_prioritizer():
    _install_prioritizer_stubs()
    if "services.management_service.prioritizer" in sys.modules:
        return importlib.reload(sys.modules["services.management_service.prioritizer"])
    return importlib.import_module("services.management_service.prioritizer")


class DummyTask:
    def __init__(self, task_type, metadata, created_at):
        self.task_type = task_type
        self.metadata = metadata
        self.created_at = created_at
        self.priority_score = 0.0


def test_calculate_impact():
    prioritizer = _import_prioritizer()
    impact = prioritizer.calculate_impact(40, 70)
    assert impact == pytest.approx(0.3, rel=1e-3)


def test_calculate_priority_bounds():
    prioritizer = _import_prioritizer()
    pr = prioritizer.calculate_priority(
        current_ffscore=40,
        expected_ffscore=70,
        task_type=prioritizer.TaskType.UPDATE_META_TAGS,
    )
    assert 0.0 <= pr <= 1.0


def test_should_auto_approve_true_for_low_risk():
    prioritizer = _import_prioritizer()
    task = DummyTask(
        prioritizer.TaskType.UPDATE_META_TAGS,
        {"impact": 0.2, "effort": 0.3},
        datetime.utcnow(),
    )
    assert prioritizer.should_auto_approve(task) is True


def test_prioritize_tasks_sorts_by_priority():
    prioritizer = _import_prioritizer()
    now = datetime.utcnow()

    task_high = DummyTask(
        prioritizer.TaskType.UPDATE_META_TAGS,
        {"current_ffscore": 30, "expected_ffscore": 80},
        now - timedelta(minutes=5),
    )
    task_low = DummyTask(
        prioritizer.TaskType.UPDATE_META_TAGS,
        {"current_ffscore": 70, "expected_ffscore": 75},
        now,
    )

    ordered = prioritizer.prioritize_tasks([task_low, task_high])
    assert ordered[0] is task_high
