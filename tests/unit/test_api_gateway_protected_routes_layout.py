"""Охрана раскладки защищённых маршрутов API Gateway.

После разделения `protected_routes.py` на модули по доменам набор путей и методов
не должен измениться. Тест проверяет, что все прежние операции на месте. Новые
маршруты добавлять можно, удалять и переименовывать нельзя без отдельного решения.
"""
import os

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://user:password@localhost:5432/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests-only-0123456789")
os.environ.setdefault("INTERNAL_API_KEY", "test-internal-key-0123456789")

import pytest

EXPECTED_OPERATIONS = [
    ("DELETE", "/api/keywords/tracked/{keyword_id}"),
    ("DELETE", "/api/projects/{project_id}"),
    ("GET", "/api/audit/history"),
    ("GET", "/api/audit/status/{uid}"),
    ("GET", "/api/auth/me"),
    ("GET", "/api/backlinks"),
    ("GET", "/api/content/optimized"),
    ("GET", "/api/dashboard"),
    ("GET", "/api/dashboard/stats"),
    ("GET", "/api/hitl/tasks"),
    ("GET", "/api/hitl/tasks/{task_id}"),
    ("GET", "/api/keywords/tracked"),
    ("GET", "/api/me"),
    ("GET", "/api/projects"),
    ("GET", "/api/projects/{project_id}"),
    ("GET", "/api/projects/{project_id}/backlinks"),
    ("GET", "/api/projects/{project_id}/content/optimized"),
    ("GET", "/api/tasks"),
    ("PATCH", "/api/auth/profile"),
    ("POST", "/api/audit/full"),
    ("POST", "/api/auth/change-password"),
    ("POST", "/api/auth/login"),
    ("POST", "/api/auth/logout"),
    ("POST", "/api/auth/refresh"),
    ("POST", "/api/auth/register"),
    ("POST", "/api/backlinks/analyze"),
    ("POST", "/api/content/analyze"),
    ("POST", "/api/hitl/approve/{task_id}"),
    ("POST", "/api/hitl/reject/{task_id}"),
    ("POST", "/api/hitl/tasks/{task_id}/approve"),
    ("POST", "/api/hitl/tasks/{task_id}/reject"),
    ("POST", "/api/keywords/search"),
    ("POST", "/api/keywords/tracked"),
    ("POST", "/api/projects"),
]


@pytest.fixture(scope="module")
def operations():
    from services.api_gateway.main import app

    spec = app.openapi()
    return {(method.upper(), path) for path, item in spec["paths"].items() for method in item}


@pytest.mark.parametrize("method,path", EXPECTED_OPERATIONS)
def test_protected_operation_is_still_registered(operations, method, path):
    assert (method, path) in operations


def test_protected_routes_are_split_by_domain():
    import importlib

    for name in (
        "auth", "dashboard", "projects", "audit", "hitl", "backlinks", "content", "keywords",
    ):
        module = importlib.import_module(f"services.api_gateway.routes.protected.{name}_routes")
        assert module.router.routes, f"{name}_routes не содержит маршрутов"
