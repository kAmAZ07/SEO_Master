"""Audit evidence JSONB round trip and competing finalization on disposable PostgreSQL.

Uses TEST_PG_ADMIN_URL and an isolated database created by the existing test fixture.
The table is an ORM test fixture; this does not validate migrations or startup DDL.
"""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from tests.integration.test_semantic_postgres import ADMIN_URL, TempDatabase
from services.audit_service import main
from services.audit_service.db.models import CrawlResult
from services.audit_service.db.session import _create_crawl_result_partitions
from services.management_service.proposal_handoff import audit_proposal_request
from shared.page_evidence import verify_evidence

pytestmark = pytest.mark.skipif(not ADMIN_URL, reason="TEST_PG_ADMIN_URL is not set")
PROJECT_ID = "11111111-1111-4111-8111-111111111111"
TASK_ID = "22222222-2222-4222-8222-222222222222"
URL = "https://example.com/catalog"


def test_competing_finalizers_preserve_one_persisted_snapshot(monkeypatch):
    async def scenario(database):
        engine = create_async_engine(database.async_url)
        try:
            async with engine.begin() as connection:
                await connection.execute(text("CREATE SCHEMA audit_schema"))
                await connection.run_sync(CrawlResult.__table__.create)
                await _create_crawl_result_partitions(connection)
            maker = async_sessionmaker(engine, expire_on_commit=False)

            @asynccontextmanager
            async def get_session():
                async with maker() as session:
                    yield session

            monkeypatch.setattr(main, "get_session", get_session)
            now = datetime.now(timezone.utc)
            async with maker() as session:
                session.add(CrawlResult(audit_id="evidence-audit", project_id=PROJECT_ID,
                                        root_url=URL, mode="full", status="running",
                                        created_at=now, updated_at=now))
                await session.commit()
            findings = [{"code": "title_too_short", "details": {"url": URL}}]

            async def finalize(title):
                return await main._finalize(
                    "evidence-audit", CrawlResult, "completed", {}, findings=findings,
                    pages=[{"url": URL, "status_code": 200,
                            "html": f'<html lang="ru"><title>{title}</title><body><main>Факты</main></body></html>'}],
                )

            results = await asyncio.gather(finalize("Первый"), finalize("Второй"))
            assert sorted(results) == [False, True]
            async with maker() as session:
                row = (await session.execute(select(CrawlResult))).scalar_one()
                envelope = row.pages[0]["page_evidence"]
                assert verify_evidence(envelope)
                assert envelope["snapshot"]["facts"]["title"] in {"Первый", "Второй"}
                crawl = {"audit_id": row.audit_id, "project_id": row.project_id,
                         "mode": row.mode, "status": row.status, "pages": row.pages,
                         "findings": row.findings}
            request = audit_proposal_request(crawl, project_id=PROJECT_ID, task_id=TASK_ID, url=URL)
            assert request.evidence[0].sha256 == envelope["content_hash"]
            assert await finalize("Поздний повтор") is False
            assert await main._update_row("evidence-audit", CrawlResult, status="running") is False
            async with maker() as session:
                row = (await session.execute(select(CrawlResult))).scalar_one()
                assert row.status == "completed" and row.pages[0]["page_evidence"] == envelope
        finally:
            await engine.dispose()

    with TempDatabase() as database:
        asyncio.run(scenario(database))
