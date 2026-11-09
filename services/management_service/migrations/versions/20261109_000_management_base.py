"""Frozen Management baseline: create an empty schema or verify/adopt exact legacy tables.

Adoption never edits existing rows, constraints or views. Partial/drifted schemas fail
before any business DDL. Downgrade of an adopted baseline preserves its tables.
"""

from datetime import datetime
from typing import cast
from uuid import UUID

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg
from sqlalchemy.dialects.postgresql.base import PGInspector
from sqlalchemy.engine import Connection

revision = "20261109_000_management_base"
down_revision = None
branch_labels = ("management",)
depends_on = None

TABLES = ("projects", "tasks", "hitl_approvals", "management_changelog")
ENUMS = {
    "tasktype": (
        "UPDATE_META",
        "UPDATE_CONTENT",
        "ADD_INTERNAL_LINKS",
        "UPDATE_SCHEMA",
        "FIX_404",
        "UPDATE_TILDA_PAGE",
        "OPTIMIZE_IMAGES",
        "FIX_BROKEN_LINKS",
    ),
    "taskstatus": (
        "PENDING",
        "IN_PROGRESS",
        "APPROVED",
        "REJECTED",
        "DEPLOYED",
        "COMPLETED",
        "FAILED",
        "CANCELLED",
    ),
    "hitlstatus": ("PENDING", "APPROVED", "REJECTED"),
}


def _snapshot() -> sa.MetaData:
    metadata = sa.MetaData(schema="public")

    def identity() -> sa.Column[UUID]:
        return sa.Column("id", pg.UUID(as_uuid=True), primary_key=True)

    def times() -> list[sa.Column[datetime]]:
        return [
            sa.Column(
                name,
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            )
            for name in ("created_at", "updated_at")
        ]

    def reference(
        name: str, target: str, delete: str, nullable: bool = False
    ) -> sa.Column[UUID]:
        return sa.Column(
            name,
            pg.UUID(as_uuid=True),
            sa.ForeignKey("public." + target, ondelete=delete),
            nullable=nullable,
        )

    def enumeration(name: str) -> pg.ENUM:
        return pg.ENUM(*ENUMS[name], name=name, schema="public", create_type=False)

    projects = sa.Table(
        "projects",
        metadata,
        identity(),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("domain", sa.String(255), nullable=False, unique=True),
        sa.Column("platform", sa.String(50), nullable=False),
        sa.Column("is_active", sa.Boolean, nullable=False),
        sa.Column("settings", pg.JSONB),
        sa.Column("metadata", pg.JSONB),
        *times(),
    )
    tasks = sa.Table(
        "tasks",
        metadata,
        identity(),
        reference("project_id", "projects.id", "CASCADE"),
        sa.Column("task_type", enumeration("tasktype"), nullable=False),
        sa.Column("status", enumeration("taskstatus"), nullable=False),
        sa.Column("url", sa.String(2048), nullable=False),
        sa.Column("title", sa.String(500)),
        sa.Column("description", sa.Text),
        sa.Column("impact_score", sa.Float),
        sa.Column("effort_score", sa.Float),
        sa.Column("priority_score", sa.Float),
        sa.Column("metadata", pg.JSONB),
        sa.Column("assigned_to", sa.String(255)),
        *times(),
        *[
            sa.Column(name, sa.DateTime(timezone=True))
            for name in ("started_at", "completed_at", "deployed_at")
        ],
    )
    approvals = sa.Table(
        "hitl_approvals",
        metadata,
        identity(),
        reference("task_id", "tasks.id", "CASCADE"),
        reference("project_id", "projects.id", "CASCADE"),
        sa.Column("status", enumeration("hitlstatus"), nullable=False),
        sa.Column("diff_data", pg.JSONB, nullable=False),
        sa.Column("impact_score", sa.Float),
        sa.Column("recommendation", sa.Text),
        sa.Column("approved_by", sa.String(255)),
        sa.Column("approved_at", sa.DateTime(timezone=True)),
        sa.Column("rejected_by", sa.String(255)),
        sa.Column("rejected_at", sa.DateTime(timezone=True)),
        sa.Column("rejection_reason", sa.Text),
        sa.Column("metadata", pg.JSONB),
        *times(),
        sa.UniqueConstraint("task_id"),
    )
    changelog = sa.Table(
        "management_changelog",
        metadata,
        identity(),
        reference("project_id", "projects.id", "CASCADE"),
        reference("task_id", "tasks.id", "SET NULL", True),
        sa.Column("entity_id", sa.String(2048), nullable=False),
        sa.Column("entity_type", sa.String(100), nullable=False),
        sa.Column("change_type", sa.String(100), nullable=False),
        sa.Column("before_value", pg.JSONB),
        sa.Column("after_value", pg.JSONB),
        sa.Column("applied", sa.Boolean, nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True)),
        sa.Column("source", sa.String(50), nullable=False),
        sa.Column("metadata", pg.JSONB),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    indices: dict[sa.Table, dict[str, tuple[str, ...]]] = {
        projects: {
            "idx_projects_domain": ("domain",),
            "idx_projects_is_active": ("is_active",),
        },
        tasks: {
            "idx_tasks_" + key: (key,)
            for key in (
                "project_id",
                "status",
                "task_type",
                "created_at",
                "priority_score",
            )
        }
        | {"idx_tasks_project_status": ("project_id", "status")},
        approvals: {
            "idx_hitl_approvals_" + key: (key,)
            for key in ("task_id", "project_id", "status", "created_at")
        },
        changelog: {
            "idx_management_changelog_" + key: (key,)
            for key in ("project_id", "task_id", "entity_id", "applied", "created_at")
        }
        | {"idx_management_changelog_project_entity": ("project_id", "entity_id")},
    }
    for table, names in indices.items():
        for name, columns in names.items():
            sa.Index(name, *(table.c[column] for column in columns))
    return metadata


def _verify(connection: Connection, metadata: sa.MetaData) -> None:
    inspector = cast(PGInspector, sa.inspect(connection))
    actual_enums = {
        item["name"]: tuple(item["labels"])
        for item in inspector.get_enums(schema="public")
    }
    if any(actual_enums.get(name) != values for name, values in ENUMS.items()):
        raise RuntimeError("MANAGEMENT_BASELINE_ENUM_DRIFT")
    for table in metadata.sorted_tables:
        actual = {
            column["name"]: column
            for column in inspector.get_columns(table.name, schema="public")
        }
        if set(actual) != set(table.c.keys()):
            raise RuntimeError("MANAGEMENT_BASELINE_COLUMN_DRIFT")
        for column in table.c:
            found = actual[column.name]
            types = [
                str(item.compile(dialect=connection.dialect)).upper()
                for item in (column.type, found["type"])
            ]
            types = [
                item.replace("DOUBLE PRECISION", "FLOAT").removeprefix("PUBLIC.")
                for item in types
            ]
            expected_default = (
                str(column.server_default.arg).lower()
                if isinstance(column.server_default, sa.DefaultClause)
                else None
            )
            actual_default = found.get("default")
            if (
                types[0] != types[1]
                or bool(column.nullable) != bool(found["nullable"])
                or expected_default
                != (actual_default.lower() if actual_default else None)
            ):
                raise RuntimeError("MANAGEMENT_BASELINE_COLUMN_DRIFT")
        if inspector.get_pk_constraint(table.name, schema="public")[
            "constrained_columns"
        ] != ["id"]:
            raise RuntimeError("MANAGEMENT_BASELINE_KEY_DRIFT")
        uniques = {
            tuple(item["column_names"])
            for item in inspector.get_unique_constraints(table.name, schema="public")
        }
        expected_uniques = {
            tuple(column.name for column in item.columns)
            for item in table.constraints
            if isinstance(item, sa.UniqueConstraint)
        }
        if uniques != expected_uniques or inspector.get_check_constraints(
            table.name, schema="public"
        ):
            raise RuntimeError("MANAGEMENT_BASELINE_KEY_DRIFT")
        if connection.scalar(
            sa.text(
                "SELECT EXISTS(SELECT 1 FROM pg_constraint WHERE conrelid=to_regclass(:table) "
                "AND (NOT convalidated OR condeferrable OR condeferred))"
            ),
            {"table": "public." + table.name},
        ):
            raise RuntimeError("MANAGEMENT_BASELINE_KEY_DRIFT")
        foreign = {
            (
                tuple(item["constrained_columns"]),
                item["referred_schema"] or "public",
                item["referred_table"],
                tuple(item["referred_columns"]),
                item["options"].get("ondelete"),
                item["options"].get("onupdate"),
            )
            for item in inspector.get_foreign_keys(table.name, schema="public")
        }
        expected_foreign = {
            (
                (fk.parent.name,),
                "public",
                fk.column.table.name,
                (fk.column.name,),
                fk.ondelete,
                fk.onupdate,
            )
            for fk in table.foreign_keys
        }
        if foreign != expected_foreign:
            raise RuntimeError("MANAGEMENT_BASELINE_KEY_DRIFT")
        indices = {
            item["name"]: item
            for item in inspector.get_indexes(table.name, schema="public")
            if not item.get("duplicates_constraint")
        }
        for index in table.indexes:
            found_index = indices.get(index.name)
            if (
                found_index is None
                or found_index["unique"]
                or found_index["column_names"] != [c.name for c in index.columns]
                or found_index.get("dialect_options", {}).get("postgresql_where")
                is not None
            ):
                raise RuntimeError("MANAGEMENT_BASELINE_INDEX_DRIFT")
        if any(item["unique"] for item in indices.values()):
            raise RuntimeError("MANAGEMENT_BASELINE_INDEX_DRIFT")


def upgrade() -> None:
    connection = op.get_bind()
    present = [
        connection.scalar(
            sa.text("SELECT to_regclass(:name)"), {"name": "public." + name}
        )
        is not None
        for name in TABLES
    ]
    if any(present) and not all(present):
        raise RuntimeError("MANAGEMENT_BASELINE_PARTIAL")
    if (
        connection.scalar(
            sa.text("SELECT to_regclass('public.management_migration_state')")
        )
        is not None
    ):
        raise RuntimeError("MANAGEMENT_BASELINE_STATE_CONFLICT")
    metadata = _snapshot()
    if all(present):
        # Prevent structural changes between reflection and adoption; no row edits.
        connection.execute(
            sa.text(
                "LOCK TABLE public.projects, public.tasks, public.hitl_approvals, "
                "public.management_changelog IN ACCESS EXCLUSIVE MODE"
            )
        )
        _verify(connection, metadata)
        if connection.scalar(
            sa.text(
                "SELECT EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname='public' AND c.relname = ANY(:tables) AND pg_get_userbyid(c.relowner) <> current_user)"
            ),
            {"tables": list(TABLES)},
        ):
            raise RuntimeError("MANAGEMENT_BASELINE_OWNER_REQUIRED")
        origin = "adopted"
    else:
        if any(
            item["name"] in ENUMS
            for item in cast(PGInspector, sa.inspect(connection)).get_enums(
                schema="public"
            )
        ):
            raise RuntimeError("MANAGEMENT_BASELINE_ENUM_CONFLICT")
        for name, values in ENUMS.items():
            pg.ENUM(*values, name=name, schema="public").create(
                connection, checkfirst=False
            )
        for table in metadata.sorted_tables:
            table.create(connection, checkfirst=False)
        origin = "created"
    op.create_table(
        "management_migration_state",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("value", sa.String(64), nullable=False),
        schema="public",
    )
    connection.execute(
        sa.text(
            "INSERT INTO public.management_migration_state (key,value) VALUES ('baseline_origin',:origin)"
        ),
        {"origin": origin},
    )


def downgrade() -> None:
    connection = op.get_bind()
    origin = connection.scalar(
        sa.text(
            "SELECT value FROM public.management_migration_state WHERE key='baseline_origin'"
        )
    )
    if origin not in {"created", "adopted"}:
        raise RuntimeError("MANAGEMENT_BASELINE_STATE_INVALID")
    if origin == "created":
        for name in TABLES:
            if connection.scalar(
                sa.text("SELECT EXISTS(SELECT 1 FROM public." + name + ")")
            ):
                raise RuntimeError("MANAGEMENT_BASELINE_DOWNGRADE_WOULD_LOSE_DATA")
        for table in reversed(_snapshot().sorted_tables):
            table.drop(connection, checkfirst=False)
        for name in reversed(tuple(ENUMS)):
            pg.ENUM(name=name, schema="public").drop(connection, checkfirst=False)
    op.drop_table("management_migration_state", schema="public")
