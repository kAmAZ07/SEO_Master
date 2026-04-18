from contextlib import asynccontextmanager
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine, async_sessionmaker

from services.audit_service.config import settings
from services.audit_service.db.models import Base

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


async def _create_crawl_result_partitions(conn) -> None:
    result = await conn.execute(
        text(
            """
            SELECT EXISTS (
                SELECT 1
                FROM pg_partitioned_table pt
                JOIN pg_class c ON c.oid = pt.partrelid
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'audit_schema' AND c.relname = 'crawl_results'
            )
            """
        )
    )
    if not result.scalar():
        return

    for remainder in range(8):
        await conn.execute(
            text(
                f"""
                CREATE TABLE IF NOT EXISTS audit_schema.crawl_results_p{remainder}
                PARTITION OF audit_schema.crawl_results
                FOR VALUES WITH (MODULUS 8, REMAINDER {remainder})
                """
            )
        )


async def init_db() -> None:
    engine = _get_engine()
    async with engine.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS audit_schema"))
        await conn.run_sync(Base.metadata.create_all)
        await _create_crawl_result_partitions(conn)
