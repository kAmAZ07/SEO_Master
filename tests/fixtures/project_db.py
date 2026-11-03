"""sqlite-стенд для тестов проектов: схемы PostgreSQL подключаются как отдельные файлы БД.

Файловая БД (а не :memory:) нужна, чтобы несколько соединений видели одни данные, это
позволяет проверять параллельные запросы. Типы и функции PostgreSQL заменены совместимыми.
"""
from __future__ import annotations

import hashlib
import os

from sqlalchemy import create_engine, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from config.database_config import Base
import database.models  # noqa: F401  (регистрирует таблицы)

SCHEMAS = ("audit_schema", "semantic_schema", "reporting_schema")


@compiles(JSONB, "sqlite")
def _jsonb_as_json(_type, _compiler, **_kw):
    return "JSON"


def make_engine(directory: str):
    engine = create_engine("sqlite:///" + os.path.join(directory, "main.db"))

    @event.listens_for(engine, "connect")
    def _prepare(dbapi_connection, _record):
        for schema in SCHEMAS:
            dbapi_connection.execute(f"ATTACH DATABASE '{os.path.join(directory, schema + '.db')}' AS {schema}")
        dbapi_connection.create_function("md5", 1, lambda value: hashlib.md5(str(value).encode()).hexdigest(), deterministic=True)

    for table in Base.metadata.sorted_tables:
        table.create(engine)
    return engine


def make_sessionmaker(engine):
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=True)
