"""Явный Alembic adapter; startup DDL и секретные провайдеры здесь запрещены."""
from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import AbstractContextManager, ExitStack, contextmanager
from pathlib import Path
import sys
from typing import TYPE_CHECKING, cast
from alembic import context
if TYPE_CHECKING:
    from sqlalchemy.engine import Connection
    from scripts.migration_policy import OwnershipFilters


class AlembicEnvironment:
    def configure_dialect(self) -> None:
        context.configure(dialect_name="postgresql", target_metadata=None)

    def configure_migrations(
        self, filters: OwnershipFilters, *, url: str | None = None,
        connection: object | None = None, offline: bool = False,
    ) -> None:
        context.configure(
            # Core stays stdlib-only; real connections enter through the outer adapter.
            connection=cast("Connection | None", connection),
            url=url, literal_binds=offline,
            dialect_opts={"paramstyle": "named"} if offline else None,
            target_metadata=None, version_table_schema="public",
            include_schemas=True, compare_type=True, compare_server_default=True,
            include_name=lambda name, type_, parents: filters.include_name(
                name, type_, {str(key): value for key, value in parents.items()},
            ),
            include_object=filters.include_object,
        )

    def configured_options(self) -> Mapping[str, object]:
        return cast(Mapping[str, object], context.get_context().opts)

    def begin_transaction(self) -> AbstractContextManager[object]:
        return context.begin_transaction()

    def run_migrations(self) -> None:
        context.run_migrations()


@contextmanager
def open_connection(url: str) -> Iterator[object]:
    from sqlalchemy import create_engine, pool
    from scripts.migration_policy import MigrationPolicyError

    with ExitStack() as resources:
        try:
            engine = create_engine(
                url, poolclass=pool.NullPool, echo=False, hide_parameters=True,
            )
            resources.callback(engine.dispose)
            connection = resources.enter_context(engine.connect())
        except Exception:
            # Driver/URL setup errors may contain the explicit credential-bearing DSN.
            raise MigrationPolicyError("MIGRATION_CONNECTION_FAILED") from None
        yield connection


def run_environment() -> None:
    root = str(Path(__file__).resolve().parents[2])
    if root not in sys.path:
        sys.path.insert(0, root)
    from scripts.migration_context import run_migration_environment
    from scripts.migration_policy import MigrationPolicyError

    configuration = context.config
    try:
        url = configuration.get_main_option("sqlalchemy.url")
    except Exception:
        # ConfigParser interpolation errors can expose fragments of the configured DSN.
        raise MigrationPolicyError("MIGRATION_DSN_INVALID") from None
    run_migration_environment(
        AlembicEnvironment(),
        url=url,
        connection=configuration.attributes.get("connection"),
        offline=context.is_offline_mode(),
        connect=open_connection,
    )


run_environment()
