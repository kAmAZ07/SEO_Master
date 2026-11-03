"""Заголовки, заданные при выбросе HTTPException, не теряются (ТЗ §3.4)."""
import os

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://user:password@localhost:5432/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-for-unit-tests-only-0123456789")
os.environ.setdefault("INTERNAL_API_KEY", "test-internal-key-0123456789")

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

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
