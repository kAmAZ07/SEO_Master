from __future__ import annotations

import os
from contextlib import contextmanager
from functools import lru_cache
from typing import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from services.project_integrations.integrations_service import IntegrationsService


def _first_non_empty(*values: str | None) -> str | None:
    for value in values:
        if value and str(value).strip():
            return str(value).strip()
    return None


def _normalize_sync_database_url(database_url: str) -> str:
    if database_url.startswith("postgresql+asyncpg://"):
        return database_url.replace("postgresql+asyncpg://", "postgresql+psycopg2://", 1)
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return database_url


def _resolve_database_url() -> str:
    database_url = _first_non_empty(
        os.getenv("DATABASE_URL"),
        os.getenv("REPORTING_DATABASE_URL"),
        os.getenv("AUDIT_DATABASE_URL"),
        os.getenv("SERVICE_DATABASE_URL"),
    )
    if not database_url:
        raise ValueError("A database URL is required to resolve project integrations at runtime")
    return _normalize_sync_database_url(database_url)


@lru_cache(maxsize=1)
def _session_factory() -> sessionmaker:
    engine = create_engine(_resolve_database_url(), pool_pre_ping=True)
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)


@contextmanager
def get_integrations_db() -> Iterator[Session]:
    session_local = _session_factory()
    db = session_local()
    try:
        yield db
    finally:
        db.close()


def load_project_integration(project_id: str, platform: str) -> dict:
    service = IntegrationsService()
    with get_integrations_db() as db:
        return service.get_credentials(db, str(project_id), platform)
