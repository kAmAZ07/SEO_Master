import importlib


def test_public_audit_rate_limiter_prefers_redis_url(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/postgres")
    monkeypatch.setenv("REDIS_URL", "redis://:redis_pass@redis:6379/0")
    monkeypatch.setenv("JWT_SECRET_KEY", "x" * 32)

    public_routes = importlib.import_module("services.api_gateway.routes.public_routes")

    calls = {}
    monkeypatch.setattr(
        public_routes,
        "get_redis_config",
        lambda: {
            "host": "localhost",
            "port": 6379,
            "password": None,
            "db": 0,
            "url": "redis://:redis_pass@redis:6379/0",
        },
    )

    class FakeRedis:
        def __init__(self, *_, **__):
            raise AssertionError("Direct Redis constructor should not be used when REDIS_URL is configured")

        @staticmethod
        def from_url(url, **kwargs):
            calls["url"] = url
            calls["kwargs"] = kwargs
            return object()

    monkeypatch.setattr(public_routes.redis, "Redis", FakeRedis)

    client = public_routes._build_redis_client()

    assert client is not None
    assert calls["url"] == "redis://:redis_pass@redis:6379/0"
    assert calls["kwargs"]["decode_responses"] is True
    assert calls["kwargs"]["socket_connect_timeout"] == 1
    assert calls["kwargs"]["socket_timeout"] == 1
