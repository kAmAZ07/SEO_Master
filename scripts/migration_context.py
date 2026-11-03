"""Stdlib-only orchestration; SQL и транзакция принадлежат Alembic revisions."""

from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
from typing import Protocol

from scripts.migration_policy import MigrationPolicyError, OwnershipFilters


class MigrationEnvironment(Protocol):
    def configure_dialect(self) -> None: ...

    def configure_migrations(
        self, filters: OwnershipFilters, *, url: str | None = None,
        connection: object | None = None, offline: bool = False,
    ) -> None: ...

    def configured_options(self) -> Mapping[str, object]: ...

    def begin_transaction(self) -> AbstractContextManager[object]: ...

    def run_migrations(self) -> None: ...


ConnectionFactory = Callable[[str], AbstractContextManager[object]]


def run_migration_environment(
    environment: MigrationEnvironment, *, url: str | None,
    connection: object | None, offline: bool, connect: ConnectionFactory,
) -> None:
    if url is not None and not isinstance(url, str):
        raise MigrationPolicyError("MIGRATION_DSN_INVALID")
    if connection is None:
        if url is None or not url.strip():
            raise MigrationPolicyError("MIGRATION_DSN_MISSING")
        if url != url.strip() or "\0" in url or len(url) > 8192:
            raise MigrationPolicyError("MIGRATION_DSN_INVALID")
    if offline and connection is not None:
        raise MigrationPolicyError("MIGRATION_OFFLINE_CONNECTION_UNSUPPORTED")

    # Alembic 1.13.3 carries revision_context for CLI AND programmatic check/revision.
    # Configuring only a dialect creates no engine/connection or reflected metadata.
    environment.configure_dialect()
    options = environment.configured_options()
    if options.get("revision_context") is not None or options.get("autogenerate") is True:
        # Also blocks revision_environment=True until a complete pure registry exists.
        raise MigrationPolicyError("MIGRATION_AUTOGENERATE_BLOCKED")

    filters = OwnershipFilters("core")
    if offline:
        try:
            environment.configure_migrations(filters, url=url, offline=True)
        except Exception:
            # Vendor URL parsing exceptions can include credential-bearing input.
            raise MigrationPolicyError("MIGRATION_DSN_INVALID") from None
        with environment.begin_transaction():
            environment.run_migrations()
    elif connection is not None:
        environment.configure_migrations(filters, connection=connection)
        with environment.begin_transaction():
            environment.run_migrations()
    else:
        # The missing-DSN invariant above is checked before the factory is touched.
        if url is None:
            raise MigrationPolicyError("MIGRATION_DSN_MISSING")
        with connect(url) as owned_connection:
            environment.configure_migrations(filters, connection=owned_connection)
            with environment.begin_transaction():
                environment.run_migrations()
