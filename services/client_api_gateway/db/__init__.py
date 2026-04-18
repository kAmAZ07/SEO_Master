import os
from typing import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import QueuePool

from config.logging_config import get_logger
from services.client_api_gateway.db.models import Base
from services.project_integrations.models import ProjectIntegration

logger = get_logger(__name__)

DATABASE_URL = os.getenv("CLIENT_DATABASE_URL") or os.getenv("DATABASE_URL")

engine = None
SessionLocal = None

if DATABASE_URL:
    engine = create_engine(
        DATABASE_URL,
        poolclass=QueuePool,
        pool_size=10,
        max_overflow=20,
        pool_pre_ping=True,
        pool_recycle=3600,
        connect_args={
            "connect_timeout": 10,
            "options": "-c timezone=utc",
        },
    )
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Generator:
    if SessionLocal is None:
        raise RuntimeError("DATABASE_URL is not configured for client_api_gateway")
    db = SessionLocal()
    try:
        yield db
    except Exception as exc:
        logger.error(f"Database session error: {exc}")
        db.rollback()
        raise
    finally:
        db.close()


def init_db():
    if engine is None:
        raise RuntimeError("DATABASE_URL is not configured for client_api_gateway")
    Base.metadata.create_all(bind=engine)
    _migrate_client_keys_secret_ref()
    ProjectIntegration.__table__.create(bind=engine, checkfirst=True)


def _migrate_client_keys_secret_ref():
    """Remove legacy plaintext HMAC storage and keep only env metadata refs."""
    if engine is None:
        return

    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE client_keys ADD COLUMN IF NOT EXISTS secret_ref VARCHAR(255)"))
        connection.execute(
            text(
                """
                UPDATE client_keys
                SET secret_ref = CONCAT('legacy-disabled:', key_id)
                WHERE secret_ref IS NULL OR secret_ref = ''
                """
            )
        )
        connection.execute(text("ALTER TABLE client_keys ALTER COLUMN secret_ref SET NOT NULL"))
        connection.execute(text("ALTER TABLE client_keys DROP COLUMN IF EXISTS secret"))
