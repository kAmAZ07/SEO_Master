"""Жизненный цикл публичного аудита в audit-service (REQ-PUBLIC §6.1–6.2, AC-02).

БД заменена sqlite (aiosqlite): проверяются статусы, отмена, дедлайн, срок хранения, слоты и
то, что наружу не попадают внутренние данные. Пайплайн подменяется управляемым запуском.
"""
import asyncio
import json
import os
import tempfile
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

pytest.importorskip("aiosqlite")
pytest.importorskip("sqlalchemy")
pytest.importorskip("pydantic_settings")
pytest.importorskip("bs4")
httpx = pytest.importorskip("httpx")

from fastapi import HTTPException
from sqlalchemy import event, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from services.audit_service import main
from services.audit_service.db.models import PublicAuditResult
from services.audit_service.public_policy import PUBLIC_MAX_CONCURRENT_JOBS
from services.audit_service.public_view import build_public_view
from services.audit_service.schemas.audit import PublicAuditRequest


@compiles(JSONB, "sqlite")
def _jsonb_as_json(_type, _compiler, **_kw):
    return "JSON"


def run(coro):
    return asyncio.run(coro)


@asynccontextmanager
async def database(monkeypatch):
    # Файловая БД: у каждого сеанса своё соединение, как в PostgreSQL (общее in-memory соединение
    # смешивало бы транзакции параллельных заданий).
    directory = tempfile.mkdtemp(prefix="audit-test-")
    engine = create_async_engine("sqlite+aiosqlite:///" + os.path.join(directory, "main.db"))
    schema_path = os.path.join(directory, "audit_schema.db")

    @event.listens_for(engine.sync_engine, "connect")
    def _attach(dbapi_connection, _record):
        dbapi_connection.execute(f"ATTACH DATABASE '{schema_path}' AS audit_schema")

    async with engine.begin() as conn:
        await conn.run_sync(PublicAuditResult.__table__.create)
    maker = async_sessionmaker(bind=engine, expire_on_commit=False, class_=AsyncSession)

    @asynccontextmanager
    async def get_session():
        async with maker() as session:
            yield session

    monkeypatch.setattr(main, "get_session", get_session)
    main._JOBS.clear()
    main._SLOTS.clear()
    try:
        yield maker
    finally:
        for task in list(main._JOBS.values()):
            task.cancel()
        await asyncio.gather(*main._JOBS.values(), return_exceptions=True)
        await engine.dispose()


def completed_result(outcome="completed"):
    return {
        "project_id": None, "mode": "public", "root_url": "https://x.test/", "outcome": outcome,
        "summary": {"coverage": {"processed": 1, "max_pages": 10}, "partial_reasons": ["deadline"] if outcome == "partial" else []},
        "findings": [{"code": "title_missing", "severity": "medium", "confidence": "high", "details": {"url": "https://x.test/"}}],
        "pages": [{"url": "https://x.test/", "status_code": 200, "title": None}],
    }


def patch_runner(monkeypatch, runner):
    monkeypatch.setattr(main, "run_public_audit_pipeline", runner)

    async def _no_publish(**_kw):
        return None

    monkeypatch.setattr(main, "publish_crawl_completed", _no_publish)


async def start(url="https://x.test/", **options):
    response = await main.start_public_audit(PublicAuditRequest(root_url=url, options=options))
    return response.audit_id


async def finish(audit_id):
    task = main._JOBS.get(audit_id)
    if task:
        await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 5)


async def row_of(maker, audit_id):
    async with maker() as session:
        return (await session.execute(select(PublicAuditResult).where(PublicAuditResult.audit_id == audit_id))).scalar_one()


async def wait_until(predicate, timeout=3.0):
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while loop.time() < end:
        if await predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not reached")


def test_options_from_client_cannot_raise_limits(monkeypatch):
    async def scenario():
        async with database(monkeypatch) as maker:
            async def runner(audit_id, on_progress=None):
                return completed_result()

            patch_runner(monkeypatch, runner)
            audit_id = await start(max_pages=100000, max_depth=10, js_render=True, respect_robots=False, timeout=300.0)
            await finish(audit_id)
            options = (await row_of(maker, audit_id)).options
            assert (options["max_pages"], options["max_depth"], options["js_render"], options["respect_robots"]) == (10, 2, False, True)

    run(scenario())


def test_running_status_exposes_real_stage_and_counters_then_completes(monkeypatch):
    async def scenario():
        async with database(monkeypatch) as maker:
            gate = asyncio.Event()

            async def runner(audit_id, on_progress=None):
                await on_progress("crawl", pages_done=3, pages_max=10)
                await gate.wait()
                return completed_result("partial")

            patch_runner(monkeypatch, runner)
            audit_id = await start()
            await wait_until(lambda: _stage_is(maker, audit_id, "crawl"))
            view = build_public_view(await row_of(maker, audit_id))
            assert view["status"] == "running" and view["stage"] == "crawl" and view["progress"] == {"pages_done": 3, "pages_max": 10}
            assert view["top_findings"] == [] and view["pages"] == []  # результат до завершения не отдаётся
            gate.set()
            await finish(audit_id)
            view = build_public_view(await row_of(maker, audit_id))
            assert view["status"] == "partial" and view["stage"] == "done"
            assert [f["code"] for f in view["top_findings"]] == ["title_missing"]

    async def _stage_is(maker, audit_id, stage):
        return (await row_of(maker, audit_id)).summary.get("progress", {}).get("stage") == stage

    run(scenario())


def test_internal_error_text_never_reaches_public_view(monkeypatch):
    async def scenario():
        async with database(monkeypatch) as maker:
            async def runner(audit_id, on_progress=None):
                raise RuntimeError("connect to 10.0.0.5:5432 failed password=hunter2")

            patch_runner(monkeypatch, runner)
            audit_id = await start()
            await finish(audit_id)
            row = await row_of(maker, audit_id)
            assert row.status == "failed"
            dumped = json.dumps(build_public_view(row))
            assert "10.0.0.5" not in dumped and "hunter2" not in dumped
            assert build_public_view(row)["summary"]["error_code"] == "internal_error"

    run(scenario())


def test_job_deadline_gives_final_state_and_releases_slot(monkeypatch):
    async def scenario():
        async with database(monkeypatch) as maker:
            monkeypatch.setattr(main, "PUBLIC_JOB_DEADLINE_S", 0.2)

            async def runner(audit_id, on_progress=None):
                await asyncio.sleep(30)

            patch_runner(monkeypatch, runner)
            audit_id = await start()
            await finish(audit_id)
            row = await row_of(maker, audit_id)
            assert row.status == "failed" and row.summary["error_code"] == "deadline_exceeded"
            assert main.active_job_count() == 0 and main._public_slots()._value == PUBLIC_MAX_CONCURRENT_JOBS

    run(scenario())


def test_cancel_running_job_releases_resources(monkeypatch):
    async def scenario():
        async with database(monkeypatch) as maker:
            cleaned = []

            async def runner(audit_id, on_progress=None):
                try:
                    await on_progress("crawl", pages_done=1, pages_max=10)
                    await asyncio.sleep(60)
                finally:
                    cleaned.append(audit_id)  # здесь освобождаются сокеты и браузеры

            patch_runner(monkeypatch, runner)
            audit_id = await start()
            await wait_until(lambda: _running(maker, audit_id))
            view = await main.cancel_public_audit(audit_id)
            assert view["status"] == "cancelled" and cleaned == [audit_id]
            assert main.active_job_count() == 0 and main._public_slots()._value == PUBLIC_MAX_CONCURRENT_JOBS
            again = await main.cancel_public_audit(audit_id)  # повторная отмена безопасна
            assert again["status"] == "cancelled"

    async def _running(maker, audit_id):
        return (await row_of(maker, audit_id)).status == "running"

    run(scenario())


def test_slots_limit_concurrency_and_queued_job_can_be_cancelled(monkeypatch):
    async def scenario():
        async with database(monkeypatch) as maker:
            gate = asyncio.Event()
            running = []

            async def runner(audit_id, on_progress=None):
                running.append(audit_id)
                await gate.wait()
                return completed_result()

            patch_runner(monkeypatch, runner)
            ids = [await start() for _ in range(PUBLIC_MAX_CONCURRENT_JOBS + 1)]
            await wait_until(lambda: _count(running, PUBLIC_MAX_CONCURRENT_JOBS))
            await asyncio.sleep(0.05)
            assert len(running) == PUBLIC_MAX_CONCURRENT_JOBS  # четвёртый ждёт слот
            queued = ids[-1]
            assert (await row_of(maker, queued)).status == "queued"
            assert (await main.cancel_public_audit(queued))["status"] == "cancelled"
            gate.set()
            for audit_id in ids[:-1]:
                await finish(audit_id)
            assert [(await row_of(maker, i)).status for i in ids] == ["completed"] * PUBLIC_MAX_CONCURRENT_JOBS + ["cancelled"]
            assert main._public_slots()._value == PUBLIC_MAX_CONCURRENT_JOBS and main.active_job_count() == 0

    async def _count(items, n):
        return len(items) >= n

    run(scenario())


async def insert_row(maker, audit_id, *, age_days=0.0, status="completed", updated_ago_s=0.0):
    now = datetime.now(timezone.utc)
    async with maker() as session:
        session.add(PublicAuditResult(
            audit_id=audit_id, project_id=None, root_url="https://x.test/", mode="public", site_type_hint="unknown",
            platform="generic", seeds=[], status=status, created_at=now - timedelta(days=age_days),
            updated_at=now - timedelta(seconds=updated_ago_s), summary={}, findings=[], pages=[], options={},
        ))
        await session.commit()


def test_retention_expired_is_410_then_purged_is_404(monkeypatch):
    async def scenario():
        async with database(monkeypatch) as maker:
            await insert_row(maker, "old-audit-00000001", age_days=8)
            await insert_row(maker, "new-audit-00000002", age_days=3)
            with pytest.raises(HTTPException) as exc:
                await main.get_public_audit("old-audit-00000001")
            assert exc.value.status_code == 410
            fresh = await main.get_public_audit("new-audit-00000002")
            assert fresh["expires_at"] > fresh["created_at"]
            assert await main.purge_expired_public_audits() == 1
            with pytest.raises(HTTPException) as exc:
                await main.get_public_audit("old-audit-00000001")
            assert exc.value.status_code == 404
            with pytest.raises(HTTPException) as exc:
                await main.get_public_audit("never-existed-0001")
            assert exc.value.status_code == 404

    run(scenario())


def test_orphaned_jobs_get_final_state_but_fresh_ones_are_kept(monkeypatch):
    async def scenario():
        async with database(monkeypatch) as maker:
            await insert_row(maker, "stale-running-0001", status="running", updated_ago_s=3600)
            await insert_row(maker, "fresh-running-0002", status="running", updated_ago_s=5)
            assert await main.recover_orphaned_public_audits() == 1
            stale = await row_of(maker, "stale-running-0001")
            assert stale.status == "failed" and stale.summary == {"error_code": "interrupted"}
            assert (await row_of(maker, "fresh-running-0002")).status == "running"

    run(scenario())


def test_http_endpoint_returns_410_for_expired(monkeypatch):
    async def scenario():
        async with database(monkeypatch) as maker:
            await insert_row(maker, "old-audit-00000003", age_days=9)
            transport = httpx.ASGITransport(app=main.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://audit") as client:
                assert (await client.get("/public/audit/old-audit-00000003")).status_code == 410
                assert (await client.get("/public/audit/unknown-0000000001")).status_code == 404

    run(scenario())


def test_public_view_whitelists_fields_and_hides_html_and_internals():
    row = SimpleNamespace(
        audit_id="abc12345", root_url="https://x.test/", status="completed",
        created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
        summary={
            "coverage": {"processed": 1}, "score": 80, "error": "psycopg: password authentication failed for 10.1.1.1",
            "robots": {"robots_url": "https://x.test/robots.txt", "available": True, "state": "ok", "error": "boom", "_parser": object},
            "internal_secret": "s3cret", "progress": {"stage": "done"},
        },
        findings=[{
            "code": "page_fetch_error", "severity": "high", "confidence": "medium", "title": "t", "description": "d",
            "details": {"url": "https://x.test/a", "error": "Traceback (most recent call last) /app/services/x.py", "html": "<html>raw</html>"},
        }],
        pages=[{"url": "https://x.test/", "status_code": 200, "title": "T", "html": "<html>raw</html>", "links": ["/a"], "error": None}],
    )
    view = build_public_view(row)
    dumped = json.dumps(view)
    for leaked in ("<html>", "s3cret", "password authentication", "Traceback", "/app/services", "boom", "10.1.1.1"):
        assert leaked not in dumped, leaked
    assert view["top_findings"][0]["evidence"] == {"url": "https://x.test/a"}
    assert set(view["pages"][0]) <= {"url", "final_url", "status_code", "title", "description", "h1", "h1_count", "error"}
