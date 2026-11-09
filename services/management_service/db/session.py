from sqlalchemy import create_engine
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import QueuePool
from typing import Generator
from fastapi import HTTPException

from services.management_service.config import settings
from config.logging_config import get_logger
from services.management_service.migrations.schema import require_runtime_role, require_schema
from shared.schema_state import require_producer_barrier

logger = get_logger(__name__)

engine = create_engine(
    str(settings.DATABASE_URL),
    poolclass=QueuePool,
    pool_size=20,
    max_overflow=40,
    pool_pre_ping=True,
    pool_recycle=3600,
    isolation_level="READ COMMITTED",
    echo=settings.ENVIRONMENT == "development",
    connect_args={
        "connect_timeout": 10,
        "options": "-c timezone=utc -c search_path=pg_catalog,public,pg_temp -c lock_timeout=5000",
    },
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    except DBAPIError as exc:
        db.rollback()
        if getattr(exc.orig, "pgcode", None) in {"55P03", "40P01", "40001"}:
            logger.warning("Management transaction concurrency conflict")
            raise HTTPException(409, "hitl_concurrency_conflict") from None
        logger.error("Management database operation failed")
        raise
    except Exception:
        logger.error("Management request failed")
        db.rollback()
        raise
    finally:
        db.close()


def init_db() -> None:
    with engine.connect() as connection:
        require_schema(connection)
        require_producer_barrier(connection)
        require_runtime_role(connection)
    logger.info("Management schema and runtime role verified")


def drop_db() -> None:
    raise RuntimeError("MANAGEMENT_RUNTIME_DDL_FORBIDDEN")


def reset_db() -> None:
    raise RuntimeError("MANAGEMENT_RUNTIME_DDL_FORBIDDEN")


def get_db_health() -> dict:
    try:
        with engine.connect() as connection:
            require_schema(connection)
            require_producer_barrier(connection)
            require_runtime_role(connection)
        return {
            "status": "healthy",
            "database": "connected",
        }
    except Exception:
        logger.error("Management database schema/role health check failed")
        return {
            "status": "unhealthy",
            "database": "disconnected",
            "error": "MANAGEMENT_DATABASE_NOT_READY",
        }

