"""Токен присоединения публичного аудита: выдаётся один раз, хранится только хэш (AL-4)."""
import asyncio
import json
import os
import tempfile
from contextlib import asynccontextmanager

import pytest

pytest.importorskip("aiosqlite")
pytest.importorskip("sqlalchemy")
pytest.importorskip("pydantic_settings")
pytest.importorskip("bs4")

from sqlalchemy import event, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from services.audit_service import main
from services.audit_service.db.models import PublicAuditResult
from services.audit_service.public_view import build_public_view
from services.audit_service.schemas.audit import PublicAuditRequest
from shared.projects.claims import hash_claim_token, token_matches


@compiles(JSONB, "sqlite")
def _jsonb_as_json(_type, _compiler, **_kw):
    return "JSON"


@asynccontextmanager
async def database(monkeypatch):
    directory = tempfile.mkdtemp(prefix="audit-claim-")
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

    async def runner(audit_id, on_progress=None):
        await asyncio.sleep(3600)

    async def no_publish(**_kw):
        return None

    monkeypatch.setattr(main, "get_session", get_session)
    monkeypatch.setattr(main, "run_public_audit_pipeline", runner)
    monkeypatch.setattr(main, "publish_crawl_completed", no_publish)
    main._JOBS.clear()
    main._SLOTS.clear()
    try:
        yield maker
    finally:
        for task in list(main._JOBS.values()):
            task.cancel()
        await asyncio.gather(*main._JOBS.values(), return_exceptions=True)
        await engine.dispose()


def run(coro):
    return asyncio.run(coro)


def test_start_returns_a_token_and_stores_only_its_hash(monkeypatch):
    async def scenario():
        async with database(monkeypatch) as maker:
            response = await main.start_public_audit(PublicAuditRequest(root_url="https://x.test/"))
            assert response.claim_token and len(response.claim_token) >= 32
            async with maker() as session:
                row = (await session.execute(select(PublicAuditResult))).scalar_one()
            assert row.claim_token_hash == hash_claim_token(response.claim_token)
            assert response.claim_token not in json.dumps({c.name: str(getattr(row, c.name)) for c in row.__table__.columns})
            assert token_matches(row.claim_token_hash, response.claim_token)

    run(scenario())


def test_every_audit_gets_its_own_token(monkeypatch):
    async def scenario():
        async with database(monkeypatch) as maker:
            tokens = set()
            for _ in range(3):
                tokens.add((await main.start_public_audit(PublicAuditRequest(root_url="https://x.test/"))).claim_token)
            assert len(tokens) == 3
            async with maker() as session:
                hashes = {row.claim_token_hash for row in (await session.execute(select(PublicAuditResult))).scalars()}
            assert len(hashes) == 3 and hashes == {hash_claim_token(t) for t in tokens}

    run(scenario())


def test_public_view_never_exposes_the_hash_or_project_link(monkeypatch):
    async def scenario():
        async with database(monkeypatch) as maker:
            response = await main.start_public_audit(PublicAuditRequest(root_url="https://x.test/"))
            async with maker() as session:
                row = (await session.execute(select(PublicAuditResult))).scalar_one()
            row.project_id = "project-123"
            dumped = json.dumps(build_public_view(row), default=str)
            assert row.claim_token_hash not in dumped
            assert response.claim_token not in dumped
            assert "claim" not in dumped and "project-123" not in dumped

    run(scenario())


def test_status_endpoint_of_the_service_does_not_return_the_token(monkeypatch):
    async def scenario():
        async with database(monkeypatch):
            started = await main.start_public_audit(PublicAuditRequest(root_url="https://x.test/"))
            view = await main.get_public_audit(started.audit_id)
            payload = json.dumps(view if isinstance(view, dict) else view.model_dump(), default=str)
            assert started.claim_token not in payload and "claim_token" not in payload

    run(scenario())
