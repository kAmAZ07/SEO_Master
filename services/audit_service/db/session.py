from contextlib import asynccontextmanager
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine, async_sessionmaker

from services.audit_service.config import settings
from shared.schema_state import require_core_schema

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def _get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    return _engine


def _get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(bind=_get_engine(), expire_on_commit=False, class_=AsyncSession)
    return _sessionmaker


@asynccontextmanager
async def get_session():
    sm = _get_sessionmaker()
    async with sm() as session:
        yield session


async def init_db() -> None:
    engine = _get_engine()
    async with engine.connect() as conn:
        await conn.run_sync(lambda sync: require_core_schema(sync, (
            "audit_schema.crawl_results", "audit_schema.public_audit_results",
            "public.processed_events", "public.failed_events"), crawl_partitions=True))
