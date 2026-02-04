import os
from sqlalchemy import create_engine, event
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, scoped_session
from sqlalchemy.pool import QueuePool
from contextlib import contextmanager

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://user:password@postgres:5432/seo_platform"
)

ASYNC_DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://")

engine = create_engine(
    DATABASE_URL,
    poolclass=QueuePool,
    pool_size=20,
    max_overflow=40,
    pool_pre_ping=True,
    pool_recycle=3600,
    echo=os.getenv("SQL_ECHO", "false").lower() == "true",
    connect_args={
        "connect_timeout": 10,
        "options": "-c timezone=Europe/Moscow"
    }
)

SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)

Session = scoped_session(SessionLocal)

Base = declarative_base()

@contextmanager
def get_db():
    db = Session()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

def get_db_dependency():
    db = Session()
    try:
        yield db
    finally:
        db.close()

@event.listens_for(engine, "connect")
def set_search_path(dbapi_conn, connection_record):
    existing_autocommit = dbapi_conn.autocommit
    dbapi_conn.autocommit = True
    cursor = dbapi_conn.cursor()
    cursor.execute("SET TIME ZONE 'Europe/Moscow'")
    cursor.close()
    dbapi_conn.autocommit = existing_autocommit

def init_db():
    Base.metadata.create_all(bind=engine)

def drop_db():
    Base.metadata.drop_all(bind=engine)

class DatabaseConfig:
    SQLALCHEMY_DATABASE_URI = DATABASE_URL
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ECHO = os.getenv("SQL_ECHO", "false").lower() == "true"
    SQLALCHEMY_POOL_SIZE = 20
    SQLALCHEMY_MAX_OVERFLOW = 40
    SQLALCHEMY_POOL_RECYCLE = 3600
    SQLALCHEMY_POOL_PRE_PING = True
    
    ALEMBIC_CONFIG = "alembic.ini"
    
    DB_SCHEMAS = {
        "audit": "audit_schema",
        "semantic": "semantic_schema",
        "reporting": "reporting_schema",
        "shared": "public"
    }

if os.getenv('ENVIRONMENT') == 'production':
    engine.pool._use_threadlocal = True
    
    DATABASE_URL_REPLICA = os.getenv("DATABASE_URL_REPLICA")
    if DATABASE_URL_REPLICA:
        replica_engine = create_engine(
            DATABASE_URL_REPLICA,
            poolclass=QueuePool,
            pool_size=10,
            max_overflow=20,
            pool_pre_ping=True,
            pool_recycle=3600
        )
