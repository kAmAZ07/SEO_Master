"""Explicit migration connection/actor, one transaction and one Management lock.

No application settings, dotenv, default DSN, implicit stamp or startup migration.
"""

import argparse
import os
from pathlib import Path
import re
from typing import Literal

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, pool, text
from sqlalchemy.engine import Connection, make_url

from services.management_service.migrations.schema import require_schema


def migrate(
    connection: Connection,
    *,
    expected_role: str,
    direction: Literal["upgrade", "downgrade"] = "upgrade",
) -> None:
    if connection.dialect.name != "postgresql" or connection.in_transaction():
        raise RuntimeError("MANAGEMENT_EXPLICIT_IDLE_POSTGRES_CONNECTION_REQUIRED")
    if connection.get_execution_options().get(
        "isolation_level"
    ) == "AUTOCOMMIT" or getattr(
        connection.connection.dbapi_connection, "autocommit", False
    ):
        raise RuntimeError("MANAGEMENT_MIGRATION_TRANSACTION_REQUIRED")
    if not expected_role or direction not in {"upgrade", "downgrade"}:
        raise RuntimeError("MANAGEMENT_MIGRATION_ACTOR_REQUIRED")
    with connection.begin():
        actor = connection.scalar(text("SELECT current_user"))
        if actor != expected_role:
            raise RuntimeError("MANAGEMENT_MIGRATION_ACTOR_MISMATCH")
        if not connection.scalar(
            text("SELECT has_schema_privilege(current_user,'public','CREATE')")
        ):
            raise RuntimeError("MANAGEMENT_MIGRATION_DDL_REQUIRED")
        connection.execute(text("SET LOCAL lock_timeout = '10s'"))
        connection.execute(text("SET LOCAL statement_timeout = '120s'"))
        connection.execute(text("SELECT pg_advisory_xact_lock(74219031109001)"))
        config = Config()
        config.set_main_option(
            "script_location", str(Path(__file__).resolve().parent).replace("%", "%%")
        )
        config.attributes.update(connection=connection, management_runner_verified=True)
        if direction == "upgrade":
            command.upgrade(config, "head")
            require_schema(connection)
        else:
            command.downgrade(config, "base")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("status", "upgrade"))
    parser.add_argument(
        "--url-env",
        required=True,
        help="Name of the explicit migration DSN environment variable",
    )
    parser.add_argument("--expected-role", required=True)
    arguments = parser.parse_args()
    engine = None
    try:
        raw_url = os.environ.get(arguments.url_env)
        if not raw_url or raw_url != raw_url.strip():
            raise RuntimeError("MANAGEMENT_MIGRATION_DSN_REQUIRED")
        url = make_url(raw_url)
        if url.get_backend_name() != "postgresql":
            raise RuntimeError("MANAGEMENT_MIGRATION_POSTGRES_REQUIRED")
        engine = create_engine(
            url,
            poolclass=pool.NullPool,
            echo=False,
            hide_parameters=True,
            connect_args={"connect_timeout": 10},
        )
        with engine.connect() as connection:
            if arguments.action == "upgrade":
                migrate(connection, expected_role=arguments.expected_role)
            else:
                if (
                    connection.scalar(text("SELECT current_user"))
                    != arguments.expected_role
                ):
                    raise RuntimeError("MANAGEMENT_MIGRATION_ACTOR_MISMATCH")
                require_schema(connection)
        print("MANAGEMENT_SCHEMA_READY")
        return 0
    except Exception as exc:
        # Vendor connection/DDL errors may contain the explicit DSN or data values.
        code = str(exc) if isinstance(exc, RuntimeError) else ""
        if not re.fullmatch(r"(?:MANAGEMENT_|AR4_)[A-Z0-9_]{1,80}", code):
            code = "MANAGEMENT_MIGRATION_FAILED"
        print(code + ": verify connection, actor and schema with an administrator")
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
