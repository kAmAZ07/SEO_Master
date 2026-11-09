"""Only the explicit Management runner may enter this independent Alembic line."""

from alembic import context
from sqlalchemy.engine import Connection

configuration = context.config
context.configure(dialect_name="postgresql", target_metadata=None)
options = context.get_context().opts
if options.get("revision_context") is not None or options.get("autogenerate") is True:
    raise RuntimeError("MIGRATION_AUTOGENERATE_BLOCKED")
connection = configuration.attributes.get("connection")
if (
    context.is_offline_mode()
    or not isinstance(connection, Connection)
    or configuration.attributes.get("management_runner_verified") is not True
    or not connection.in_transaction()
):
    raise RuntimeError("MANAGEMENT_EXPLICIT_RUNNER_REQUIRED")
context.configure(
    connection=connection,
    target_metadata=None,
    version_table="management_alembic_version",
    version_table_schema="public",
    include_schemas=True,
)
with context.begin_transaction():
    context.run_migrations()
