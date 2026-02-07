from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import QueuePool
from typing import Generator
import os

from services.client_api_gateway.db.models import Base
from config.logging_config import get_logger

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
