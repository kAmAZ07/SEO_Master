"""Заголовки, заданные при выбросе HTTPException, не теряются (ТЗ §3.4)."""
import os
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://user:password@localhost:5432/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests-only-0123456789")
os.environ.setdefault("INTERNAL_API_KEY", "test-internal-key-0123456789")

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from services.api_gateway.middleware.error_handler import setup_error_handlers


@pytest.fixture()
def client():
    app = FastAPI()
    setup_error_handlers(app)

    @app.get("/limited")
    def limited():
        raise HTTPException(
            status_code=429,
            detail="Too many requests",
            headers={"Retry-After": "42", "X-RateLimit-Remaining": "0"},
        )

    @app.get("/unauthorized")
    def unauthorized():
        raise HTTPException(
            status_code=401, detail="Could not validate credentials", headers={"WWW-Authenticate": "Bearer"}
        )

    @app.get("/plain")
    def plain():
        raise HTTPException(status_code=404, detail="Not found")

    return TestClient(app, raise_server_exceptions=False)


def test_retry_after_and_rate_limit_headers_are_preserved(client):
    response = client.get("/limited")
    assert response.status_code == 429
    assert response.headers["retry-after"] == "42"
    assert response.headers["x-ratelimit-remaining"] == "0"
    assert response.json()["error"]["code"] == 429


def test_www_authenticate_is_preserved_on_401(client):
    response = client.get("/unauthorized")
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_exception_without_headers_still_works(client):
    response = client.get("/plain")
    assert response.status_code == 404
    assert "retry-after" not in response.headers
    assert response.json()["error"]["message"] == "Not found"


@pytest.mark.parametrize("kind", ["database", "unexpected"])
def test_failed_request_keeps_error_and_query_details_out_of_http_and_logs(monkeypatch, kind):
    from services.api_gateway.middleware import error_handler
    from services.api_gateway.middleware import logging as request_logging

    captured = Mock()
    monkeypatch.setattr(error_handler, "logger", captured)
    monkeypatch.setattr(request_logging, "logger", captured)
    monkeypatch.setattr(error_handler.settings, "ENVIRONMENT", "development")
    app = FastAPI()
    setup_error_handlers(app)
    app.add_middleware(request_logging.LoggingMiddleware)

    @app.get("/fixture")
    def fixture():
        cause = RuntimeError("fixture-private-driver-detail")
        if kind == "database":
            raise OperationalError("SELECT fixture", {"value": "fixture-private-sql-param"}, cause)
        raise cause

    with TestClient(app) as client:
        response = client.get("/fixture", params={"marker": "fixture-private-query"})

    assert response.status_code == 500
    body = response.json()["error"]
    assert body["message"] == ("Database error occurred" if kind == "database" else "Internal server error")
    assert response.headers["X-Request-ID"] == body["request_id"]
    assert "fixture-private-" not in response.text + str(captured.mock_calls)
    assert kind == "database" or "RuntimeError" in str(captured.mock_calls)


def test_access_log_keeps_request_path_without_query():
    from shared.tilda_contract import CallbackAccessFilter

    record = logging.LogRecord("uvicorn.access", logging.INFO, "fixture", 1, '%s - "%s %s HTTP/%s" %d',
                               ("fixture", "GET", "/fixture?marker=fixture-private-query", "1.1", 500), None)
    assert CallbackAccessFilter().filter(record)
    assert "/fixture HTTP/1.1" in record.getMessage()
    assert "fixture-private-query" not in record.getMessage()


def test_integration_write_failure_keeps_storage_error_out_of_log(monkeypatch):
    from services.api_gateway.middleware import error_handler
    from services.api_gateway.middleware import logging as request_logging
    from services.api_gateway.routes import integrations_routes

    captured = Mock()
    for module in (error_handler, request_logging, integrations_routes):
        monkeypatch.setattr(module, "logger", captured)
    write = AsyncMock(side_effect=OperationalError("UPDATE fixture", {"value": "fixture-private-sql-param"},
                                                   RuntimeError("fixture-private-driver-detail")))
    monkeypatch.setattr(integrations_routes.service, "save_gsc_credentials", write)
    monkeypatch.setattr(integrations_routes, "_get_owned_project", Mock())
    app = FastAPI()
    app.add_middleware(request_logging.LoggingMiddleware)
    setup_error_handlers(app)
    app.include_router(integrations_routes.router)
    app.dependency_overrides[integrations_routes.get_current_user] = lambda: SimpleNamespace(id="fixture-user")
    app.dependency_overrides[integrations_routes.get_db] = lambda: None

    with TestClient(app) as client:
        response = client.post("/api/projects/project-1/integrations/gsc",
                               json={"propertyUrl": "https://fixture.test/", "credentialsJson": "{}"})

    write.assert_awaited_once()
    assert response.status_code == 500
    assert response.json()["error"]["message"] == "Integration request failed"
    assert "fixture-private-" not in response.text + str(captured.mock_calls)
