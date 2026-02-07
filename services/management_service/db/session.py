<<<<<<< HEAD
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import QueuePool
from typing import Generator

from services.management_service.config import settings
from config.logging_config import get_logger

logger = get_logger(__name__)

engine = create_engine(
    settings.DATABASE_URL,
    poolclass=QueuePool,
    pool_size=20,
    max_overflow=40,
    pool_pre_ping=True,
    pool_recycle=3600,
    echo=settings.ENVIRONMENT == "development",
    connect_args={
        "connect_timeout": 10,
        "options": "-c timezone=utc"
    }
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)


def get_db() -> Generator[Session, None, None]:
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
    from services.management_service.db.models import Base

    try:
        Base.metadata.create_all(bind=engine)
        logger.info("Database tables created successfully")
    except Exception as exc:
        logger.error(f"Failed to create database tables: {exc}")
        raise


def drop_db():
    from services.management_service.db.models import Base

    try:
        Base.metadata.drop_all(bind=engine)
        logger.warning("Database tables dropped")
    except Exception as exc:
        logger.error(f"Failed to drop database tables: {exc}")
        raise


def reset_db():
    drop_db()
    init_db()
    logger.info("Database reset completed")


def get_db_health() -> dict:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return {
            "status": "healthy",
            "database": "connected"
        }
    except Exception as exc:
        logger.error(f"Database health check failed: {exc}")
        return {
            "status": "unhealthy",
            "database": "disconnected",
            "error": str(exc)
=======
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.pool import QueuePool
from typing import Generator

from services.management_service.config import settings
from config.loggingconfig import get_logger

logger = get_logger(__name__)

engine = create_engine(
    settings.DATABASE_URL,
    poolclass=QueuePool,
    pool_size=20,
    max_overflow=40,
    pool_pre_ping=True,
    pool_recycle=3600,
    echo=settings.ENVIRONMENT == "development",
    connect_args={
        "connect_timeout": 10,
        "options": "-c timezone=utc"
    }
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    except Exception as e:
        logger.error(f"Database session error: {e}")
        db.rollback()
        raise
    finally:
        db.close()


def init_db():
    from services.management_service.db.models import Base
    
    try:
        Base.metadata.create_all(bind=engine)
        logger.info("Database tables created successfully")
    except Exception as e:
        logger.error(f"Failed to create database tables: {e}")
        raise


def drop_db():
    from services.management_service.db.models import Base
    
    try:
        Base.metadata.drop_all(bind=engine)
        logger.warning("Database tables dropped")
    except Exception as e:
        logger.error(f"Failed to drop database tables: {e}")
        raise


def reset_db():
    drop_db()
    init_db()
    logger.info("Database reset completed")


def get_db_health() -> dict:
    try:
        with engine.connect() as connection:
            connection.execute("SELECT 1")
        return {
            "status": "healthy",
            "database": "connected"
        }
    except Exception as e:
        logger.error(f"Database health check failed: {e}")
        return {
            "status": "unhealthy",
            "database": "disconnected",
            "error": str(e)
>>>>>>> b1c8d319cacd3de5d12893d9b89864aee6368263
        }
