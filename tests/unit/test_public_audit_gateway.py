"""Публичные маршруты шлюза: ограничение частоты, защита от подделки заголовков, статусы (AC-02, REQ-PUBLIC §6)."""
import asyncio
import json

import pytest

httpx = pytest.importorskip("httpx")
respx = pytest.importorskip("respx")
pytest.importorskip("pydantic_settings")

from fastapi import FastAPI

from services.api_gateway.routes import public_routes
from services.api_gateway.config import settings

AUDIT = settings.AUDIT_SERVICE_URL
LIMIT = settings.PUBLIC_RATE_LIMIT


class FakeRedis:
    """Минимальная модель Redis: INCR/EXPIRE/TTL/DECR без изменения между вызовами (как Lua-скрипт)."""

    def __init__(self):
        self.counters, self.ttls, self.fail = {}, {}, False

    def eval(self, script, numkeys, key, window):
        if self.fail:
            raise ConnectionError("redis down")
        self.counters[key] = self.counters.get(key, 0) + 1
        self.ttls.setdefault(key, int(window))
        return [self.counters[key], self.ttls[key]]

    def decr(self, key):
        self.counters[key] = self.counters.get(key, 0) - 1

    def get(self, key):
        if self.fail:
            raise ConnectionError("redis down")
        return self.counters.get(key)

    def ttl(self, key):
        return self.ttls.get(key, -2)


@pytest.fixture
def redis_fake(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(public_routes, "redis_client", fake)
    return fake


def make_client(peer="203.0.113.5"):
    app = FastAPI()
    app.include_router(public_routes.router)
    transport = httpx.ASGITransport(app=app, client=(peer, 40000))
    return httpx.AsyncClient(transport=transport, base_url="http://gateway")


def run(coro):
    return asyncio.run(coro)


def audit_ok():
    return respx.post(f"{AUDIT}/audit/public").respond(200, json={"audit_id": "0123abcd-0000-4000-8000-000000000001", "status": "queued", "mode": "public"})


@respx.mock
def test_sixth_accepted_run_gets_429_with_retry_after(redis_fake):
    audit_ok()

    async def scenario():
        async with make_client() as client:
            statuses = [(await client.post("/api/public/quick-audit", json={"url": "https://example.com/"})) for _ in range(LIMIT + 1)]
            return statuses

    responses = run(scenario())
    assert [r.status_code for r in responses] == [200] * LIMIT + [429]
    assert 0 < int(responses[-1].headers["Retry-After"]) <= settings.PUBLIC_RATE_LIMIT_WINDOW_SECONDS
    assert responses[0].json()["status"] == "queued"


@respx.mock
def test_forged_forwarded_headers_from_untrusted_peer_do_not_bypass_limit(redis_fake):
    audit_ok()

    async def scenario():
        async with make_client(peer="203.0.113.5") as client:
            out = []
            for i in range(LIMIT + 3):
                headers = {"X-Forwarded-For": f"198.51.100.{i}", "X-Real-IP": f"198.51.100.{100 + i}"}
                out.append((await client.post("/api/public/quick-audit", json={"url": "https://example.com/"}, headers=headers)).status_code)
            return out

    codes = run(scenario())
    assert codes.count(200) == LIMIT and codes.count(429) == 3


@respx.mock
def test_behind_trusted_proxy_the_real_client_is_the_rightmost_untrusted_address(redis_fake):
    audit_ok()

    async def scenario():
        async with make_client(peer="10.0.0.2") as client:  # nginx в docker-сети
            out = []
            for i in range(LIMIT + 2):
                # Слева — подделка клиента (меняется), справа — адрес, который дописал nginx.
                headers = {"X-Forwarded-For": f"9.9.9.{i}, 198.51.100.7"}
                out.append((await client.post("/api/public/quick-audit", json={"url": "https://example.com/"}, headers=headers)).status_code)
            other = await client.post("/api/public/quick-audit", json={"url": "https://example.com/"},
                                      headers={"X-Forwarded-For": "198.51.100.8"})
            return out, other.status_code

    codes, other = run(scenario())
    assert codes.count(200) == LIMIT and codes.count(429) == 2
    assert other == 200  # другой реальный клиент имеет свой счётчик


@respx.mock
def test_concurrent_requests_cannot_exceed_limit(redis_fake):
    audit_ok()

    async def scenario():
        async with make_client() as client:
            results = await asyncio.gather(*[client.post("/api/public/quick-audit", json={"url": "https://example.com/"}) for _ in range(25)])
            return [r.status_code for r in results]

    codes = run(scenario())
    assert codes.count(200) == LIMIT and codes.count(429) == 25 - LIMIT


@respx.mock
def test_ipv6_addresses_from_one_64_prefix_share_a_counter(redis_fake):
    audit_ok()

    async def scenario():
        out = []
        for i in range(LIMIT + 1):
            async with make_client(peer=f"2001:db8:abcd:12::{i + 1}") as client:
                out.append((await client.post("/api/public/quick-audit", json={"url": "https://example.com/"})).status_code)
        return out

    assert run(scenario()).count(429) == 1


@respx.mock
def test_redis_outage_is_bounded_503_and_audit_service_is_not_called(redis_fake):
    route = audit_ok()
    redis_fake.fail = True

    async def scenario():
        async with make_client() as client:
            return await client.post("/api/public/quick-audit", json={"url": "https://example.com/"})

    response = run(scenario())
    assert response.status_code == 503 and response.headers["Retry-After"]
    assert not route.called


@respx.mock
@pytest.mark.parametrize("url", ["ftp://example.com/", "http://user:pw@example.com/", "http://localhost/", "http://127.0.0.1/",
                                 "http://169.254.169.254/", "http://[::1]/", "http://2130706433/", "https://example.com:8443/", "not a url"])
def test_invalid_or_internal_urls_are_rejected_and_do_not_consume_quota(redis_fake, url):
    route = audit_ok()

    async def scenario():
        async with make_client() as client:
            rejected = await client.post("/api/public/quick-audit", json={"url": url})
            info = await client.get("/api/public/rate-limit-info")
            return rejected, info

    rejected, info = run(scenario())
    assert rejected.status_code == 422 and not route.called
    assert info.json()["remaining"] == LIMIT


@respx.mock
def test_url_path_case_is_preserved():
    async def scenario():
        async with make_client() as client:
            return await client.post("/api/public/quick-audit", json={"url": "https://example.com/Case/Sensitive?Q=A"})

    route = audit_ok()
    public_routes.redis_client = FakeRedis()
    run(scenario())
    assert json.loads(route.calls[0].request.content)["root_url"] == "https://example.com/Case/Sensitive?Q=A"


@respx.mock
def test_client_cannot_send_limits_to_audit_service(redis_fake):
    route = audit_ok()

    async def scenario():
        async with make_client() as client:
            await client.post("/api/public/quick-audit", json={"url": "https://example.com/", "options": {"max_pages": 100000}, "max_pages": 5})

    run(scenario())
    assert "options" not in json.loads(route.calls[0].request.content)


@respx.mock
def test_audit_service_failure_refunds_quota(redis_fake):
    respx.post(f"{AUDIT}/audit/public").respond(500, text="boom")

    async def scenario():
        async with make_client() as client:
            failed = await client.post("/api/public/quick-audit", json={"url": "https://example.com/"})
            info = await client.get("/api/public/rate-limit-info")
            return failed, info

    failed, info = run(scenario())
    assert failed.status_code == 502 and "boom" not in failed.text
    assert info.json()["remaining"] == LIMIT


UID = "0123abcd-0000-4000-8000-000000000001"


def view(status="completed", **extra):
    base = {"audit_id": UID, "root_url": "https://example.com/", "mode": "public", "status": status, "stage": None, "progress": {},
            "summary": {}, "top_findings": [], "findings_count": 0, "pages": [], "created_at": "2026-11-01T10:00:00+00:00",
            "updated_at": "2026-11-01T10:01:00+00:00", "expires_at": "2026-11-08T10:00:00+00:00"}
    base.update(extra)
    return base


@respx.mock
def test_status_progress_follows_stage_and_counters():
    respx.get(f"{AUDIT}/public/audit/{UID}").respond(200, json=view("running", stage="crawl", progress={"pages_done": 3, "pages_max": 10}))

    async def scenario():
        async with make_client() as client:
            return (await client.get(f"/api/public/audit-status/{UID}")).json()

    body = run(scenario())
    assert body["status"] == "running" and body["stage"] == "crawl" and body["progress"] == 35
    assert body["counters"] == {"pages_done": 3, "pages_max": 10} and body["results"] is None


@respx.mock
@pytest.mark.parametrize("status", ["completed", "partial", "failed", "cancelled"])
def test_final_statuses_are_passed_through_with_report(status):
    finding = {"code": "title_missing", "severity": "medium", "confidence": "high", "evidence": {"url": "https://example.com/"}}
    respx.get(f"{AUDIT}/public/audit/{UID}").respond(200, json=view(status, top_findings=[finding], findings_count=1, summary={"checks": {"robots": {"status": "passed"}}, "partial_reasons": ["deadline"]}))

    async def scenario():
        async with make_client() as client:
            return (await client.get(f"/api/public/audit-status/{UID}")).json()

    body = run(scenario())
    assert body["status"] == status and body["progress"] == 100
    assert body["results"]["top_findings"][0]["evidence"] == {"url": "https://example.com/"}
    assert body["results"]["checks"]["robots"]["status"] == "passed" and body["results"]["partial_reasons"] == ["deadline"]
    assert body["expires_at"]


@respx.mock
def test_expired_and_unknown_audits_have_published_answers():
    respx.get(f"{AUDIT}/public/audit/{UID}").respond(410, json={"detail": "audit_expired"})
    respx.get(f"{AUDIT}/public/audit/ffffffff-0000-4000-8000-00000000000f").respond(404, json={"detail": "audit_not_found"})

    async def scenario():
        async with make_client() as client:
            return (await client.get(f"/api/public/audit-status/{UID}"),
                    await client.get("/api/public/audit-status/ffffffff-0000-4000-8000-00000000000f"))

    expired, unknown = run(scenario())
    assert expired.status_code == 410 and expired.json()["detail"]["error"] == "audit_expired"
    assert unknown.status_code == 404


@respx.mock
@pytest.mark.parametrize("uid", ["..%2F..%2Fhealth", "abc", "not-a-uid-at-all!", "a" * 200])
def test_malformed_uid_never_reaches_audit_service(uid):
    route = respx.route(host="localhost").respond(200, json={})

    async def scenario():
        async with make_client() as client:
            return await client.get(f"/api/public/audit-status/{uid}")

    assert run(scenario()).status_code in (404, 405)
    assert not route.called


@respx.mock
def test_cancel_passes_through():
    respx.post(f"{AUDIT}/public/audit/{UID}/cancel").respond(200, json=view("cancelled"))

    async def scenario():
        async with make_client() as client:
            return (await client.post(f"/api/public/audit/{UID}/cancel")).json()

    assert run(scenario())["status"] == "cancelled"


@respx.mock
def test_claim_token_is_passed_to_the_creator_once_and_never_via_status(redis_fake):
    respx.post(f"{AUDIT}/audit/public").respond(
        200, json={"audit_id": "0123abcd-0000-4000-8000-000000000001", "status": "queued", "mode": "public", "claim_token": "tok-123"}
    )
    respx.get(f"{AUDIT}/public/audit/0123abcd-0000-4000-8000-000000000001").respond(200, json={"status": "queued", "root_url": "https://example.com/"})

    async def scenario():
        async with make_client() as client:
            started = await client.post("/api/public/quick-audit", json={"url": "https://example.com/"})
            status = await client.get("/api/public/audit-status/0123abcd-0000-4000-8000-000000000001")
            return started, status

    started, status = run(scenario())
    assert started.json()["claim_token"] == "tok-123"
    assert "tok-123" not in status.text and "claim" not in status.text


@respx.mock
def test_missing_claim_token_from_an_old_audit_service_is_null_not_an_error(redis_fake):
    audit_ok()

    async def scenario():
        async with make_client() as client:
            return await client.post("/api/public/quick-audit", json={"url": "https://example.com/"})

    response = run(scenario())
    assert response.status_code == 200 and response.json()["claim_token"] is None
