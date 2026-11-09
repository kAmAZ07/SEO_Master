"""Client storage foundation in the existing control-plane migration line.

Frozen schema, no runtime ORM/settings import. Exact legacy adoption preserves data;
plaintext-secret/partial/drifted schemas require explicit review, never implicit DROP.
"""
from datetime import datetime
from uuid import UUID

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.engine import Connection

revision = "20261109_004_client_storage"
down_revision = "20261109_003_execution"
branch_labels = None
depends_on = None
TABLES = ("client_keys", "deployment_log")


def snapshot() -> sa.MetaData:
    metadata = sa.MetaData(schema="public")
    def identity() -> sa.Column[UUID]:
        return sa.Column("id", pg.UUID(as_uuid=True), primary_key=True)
    def date(name: str, *, required: bool = False, default: bool = False) -> sa.Column[datetime]:
        return sa.Column(name, sa.DateTime(timezone=True), nullable=not required,
                         server_default=sa.func.now() if default else None)
    key = sa.Table("client_keys", metadata, identity(),
        sa.Column("project_id", sa.String(255), nullable=False),
        sa.Column("key_id", sa.String(100), nullable=False, unique=True),
        sa.Column("secret_ref", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False), date("created_at", required=True, default=True),
        date("expires_at", required=True), date("rotated_at"), date("grace_until"), date("last_used_at"),
        sa.Column("metadata", pg.JSONB))
    log = sa.Table("deployment_log", metadata, identity(),
        sa.Column("project_id", sa.String(255), nullable=False), sa.Column("task_id", sa.String(255)),
        sa.Column("change_type", sa.String(50), nullable=False), sa.Column("entity_id", sa.String(2048), nullable=False),
        sa.Column("entity_type", sa.String(100), nullable=False), sa.Column("status", sa.String(50), nullable=False),
        sa.Column("error_message", sa.Text), sa.Column("changes", pg.JSONB, nullable=False), sa.Column("metadata", pg.JSONB),
        sa.Column("source_ip", sa.String(45)), sa.Column("user_agent", sa.String(512)), sa.Column("correlation_id", sa.String(255)),
        date("created_at", required=True, default=True), date("applied_at"))
    sa.Index("idx_client_keys_project", key.c.project_id)
    sa.Index("idx_client_keys_active", key.c.project_id, key.c.is_active)
    for suffix, column in (("project", "project_id"), ("status", "status"), ("change_type", "change_type"), ("created_at", "created_at")):
        sa.Index("idx_deploy_log_" + suffix, log.c[column])
    return metadata


def verify(connection: Connection, metadata: sa.MetaData) -> None:
    inspector = sa.inspect(connection)
    for table in metadata.sorted_tables:
        columns = {item["name"]: item for item in inspector.get_columns(table.name, schema="public")}
        if set(columns) != set(table.c.keys()):
            raise RuntimeError("CLIENT_BASELINE_COLUMN_DRIFT")
        for column in table.c:
            found = columns[column.name]
            expected_default = str(column.server_default.arg).lower() if isinstance(column.server_default, sa.DefaultClause) else None
            actual_default = found.get("default")
            if (str(column.type.compile(dialect=connection.dialect)).upper() != str(found["type"].compile(dialect=connection.dialect)).upper()
                    or column.nullable != found["nullable"] or expected_default != (actual_default.lower() if actual_default else None)):
                raise RuntimeError("CLIENT_BASELINE_COLUMN_DRIFT")
        if inspector.get_pk_constraint(table.name, schema="public")["constrained_columns"] != ["id"]:
            raise RuntimeError("CLIENT_BASELINE_KEY_DRIFT")
        uniques = {tuple(item["column_names"]) for item in inspector.get_unique_constraints(table.name, schema="public")}
        if uniques != ({("key_id",)} if table.name == "client_keys" else set()) or inspector.get_foreign_keys(table.name, schema="public") or inspector.get_check_constraints(table.name, schema="public"):
            raise RuntimeError("CLIENT_BASELINE_KEY_DRIFT")
        if connection.scalar(sa.text("SELECT EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid=to_regclass(:name) AND (NOT convalidated OR condeferrable OR condeferred))"), {"name": "public." + table.name}):
            raise RuntimeError("CLIENT_BASELINE_KEY_DRIFT")
        indexes = {item["name"]: item for item in inspector.get_indexes(table.name, schema="public") if not item.get("duplicates_constraint")}
        expected_indexes = {index.name: [column.name for column in index.columns] for index in table.indexes}
        if set(indexes) != set(expected_indexes) or any(indexes[name]["column_names"] != column_names or indexes[name]["unique"]
            or indexes[name].get("dialect_options", {}).get("postgresql_where") is not None for name, column_names in expected_indexes.items()):
            raise RuntimeError("CLIENT_BASELINE_INDEX_DRIFT")
    if connection.scalar(sa.text("SELECT EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relname=ANY(:names) AND (c.relkind!='r' OR pg_get_userbyid(c.relowner)<>current_user))"), {"names": list(TABLES)}):
        raise RuntimeError("CLIENT_BASELINE_OWNER_REQUIRED")


def upgrade() -> None:
    connection = op.get_bind()
    present = [connection.scalar(sa.text("SELECT to_regclass(:name)"), {"name": "public." + name}) is not None for name in TABLES]
    if any(present) and not all(present):
        raise RuntimeError("CLIENT_BASELINE_PARTIAL")
    if all(present):
        connection.execute(sa.text("LOCK TABLE public.client_keys, public.deployment_log IN ACCESS EXCLUSIVE MODE"))
        verify(connection, snapshot())
        origin = "adopted"
    else:
        for table in snapshot().sorted_tables:
            table.create(connection, checkfirst=False)
        origin = "created"
    connection.execute(sa.text("INSERT INTO public.management_migration_state(key,value) VALUES ('client_baseline_origin',:origin)"), {"origin": origin})


def downgrade() -> None:
    connection = op.get_bind()
    origin = connection.scalar(sa.text("SELECT value FROM public.management_migration_state WHERE key='client_baseline_origin'"))
    if origin not in {"created", "adopted"}:
        raise RuntimeError("CLIENT_BASELINE_STATE_INVALID")
    if origin == "created":
        connection.execute(sa.text("LOCK TABLE public.client_keys, public.deployment_log IN ACCESS EXCLUSIVE MODE"))
        for name in TABLES:
            if connection.scalar(sa.text("SELECT EXISTS(SELECT 1 FROM public." + name + ")")):
                raise RuntimeError("CLIENT_BASELINE_DOWNGRADE_WOULD_LOSE_DATA")
        for table in reversed(snapshot().sorted_tables):
            table.drop(connection, checkfirst=False)
    connection.execute(sa.text("DELETE FROM public.management_migration_state WHERE key='client_baseline_origin'"))
