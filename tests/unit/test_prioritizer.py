import importlib
import sys
import types
import enum
from datetime import datetime, timedelta, timezone

import pytest


_STUB_MODULE_NAMES = [
    "sqlalchemy",
    "sqlalchemy.orm",
    "services.management_service.config",
    "services.management_service.db.models",
    "config.logging_config",
]


@pytest.fixture(autouse=True)
def _restore_stubbed_modules():
    previous = {name: sys.modules.get(name) for name in _STUB_MODULE_NAMES}
    yield
    for name, module in previous.items():
        if module is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = module


def _install_prioritizer_stubs():
    sqlalchemy_module = types.ModuleType("sqlalchemy")
    sqlalchemy_orm_module = types.ModuleType("sqlalchemy.orm")
    config_module = types.ModuleType("services.management_service.config")
    db_models_module = types.ModuleType("services.management_service.db.models")
    logging_module = types.ModuleType("config.logging_config")

    class Settings:
        TASK_PRIORITY_IMPACT_WEIGHT = 0.6
        TASK_PRIORITY_URGENCY_WEIGHT = 0.3
        TASK_PRIORITY_EFFORT_WEIGHT = 0.1
        HITL_AUTO_APPROVE_LOW_RISK = True

    class DummyLogger:
        def info(self, *args, **kwargs):
            return None

        def error(self, *args, **kwargs):
            return None

        def warning(self, *args, **kwargs):
            return None

    class TaskType(enum.Enum):
        UPDATE_META = "UPDATE_META"
        UPDATE_CONTENT = "UPDATE_CONTENT"
        ADD_INTERNAL_LINKS = "ADD_INTERNAL_LINKS"
        UPDATE_SCHEMA = "UPDATE_SCHEMA"
        FIX_404 = "FIX_404"
        UPDATE_TILDA_PAGE = "UPDATE_TILDA_PAGE"
        OPTIMIZE_IMAGES = "OPTIMIZE_IMAGES"
        FIX_BROKEN_LINKS = "FIX_BROKEN_LINKS"

    class TaskStatus(enum.Enum):
        PENDING = "PENDING"

    class Task:
        pass

    class Session:
        pass

    sqlalchemy_orm_module.Session = Session
    sqlalchemy_module.orm = sqlalchemy_orm_module

    config_module.settings = Settings()
    db_models_module.TaskType = TaskType
    db_models_module.TaskStatus = TaskStatus
    db_models_module.Task = Task
    logging_module.get_logger = lambda *args, **kwargs: DummyLogger()

    sys.modules["sqlalchemy"] = sqlalchemy_module
    sys.modules["sqlalchemy.orm"] = sqlalchemy_orm_module
    sys.modules["services.management_service.config"] = config_module
    sys.modules["services.management_service.db.models"] = db_models_module
    sys.modules["config.logging_config"] = logging_module


def _import_prioritizer():
    _install_prioritizer_stubs()
    module_name = "services.management_service.prioritizer"
    if module_name in sys.modules:
        return importlib.reload(sys.modules[module_name])
    return importlib.import_module(module_name)


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
    priority = prioritizer.calculate_priority(
        current_ffscore=40,
        expected_ffscore=70,
        task_type=prioritizer.TaskType.UPDATE_META,
    )
    assert 0.0 <= priority <= 1.0


def test_should_auto_approve_true_for_low_risk():
    prioritizer = _import_prioritizer()
    task = DummyTask(
        prioritizer.TaskType.UPDATE_META,
        {"impact": 0.2, "effort": 0.3},
        datetime.now(timezone.utc),
    )
    assert prioritizer.should_auto_approve(task) is True


def test_prioritize_tasks_sorts_by_priority():
    prioritizer = _import_prioritizer()
    now = datetime.now(timezone.utc)

    task_high = DummyTask(
        prioritizer.TaskType.UPDATE_META,
        {"current_ffscore": 30, "expected_ffscore": 80},
        now - timedelta(minutes=5),
    )
    task_low = DummyTask(
        prioritizer.TaskType.UPDATE_META,
        {"current_ffscore": 70, "expected_ffscore": 75},
        now,
    )

    ordered = prioritizer.prioritize_tasks([task_low, task_high])
    assert ordered[0] is task_high

