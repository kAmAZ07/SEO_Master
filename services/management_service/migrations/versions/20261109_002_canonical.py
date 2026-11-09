"""Canonical FK for a new Management installation; legacy transfer remains gated.

UUID remains the public project_id. A stored, generated text representation matches
Core's varchar(36) key exactly; callers cannot submit a different canonical binding.
No host matching, UUID remapping, row deletion or signed payload rewriting.
"""

from alembic import op
import sqlalchemy as sa

revision = "20261109_002_canonical"
down_revision = "20261109_001_hitl_ledger"
branch_labels = None
depends_on = None

TABLES = ("tasks", "hitl_approvals", "management_changelog", "hitl_history")


def upgrade() -> None:
    connection = op.get_bind()
    # Do not turn a same-host map (or accidental UUID equality) into tenant ownership.
    # Exclude concurrent legacy writes while checking and replacing the FK graph.
    connection.execute(
        sa.text(
            "LOCK TABLE public.projects, public.tasks, public.hitl_approvals, "
            "public.management_changelog, public.hitl_history, public.management_outbox "
            "IN ACCESS EXCLUSIVE MODE"
        )
    )
    for name in ("projects", *TABLES, "management_outbox"):
        if connection.scalar(
            sa.text("SELECT EXISTS(SELECT 1 FROM public." + name + ")")
        ):
            raise RuntimeError("MANAGEMENT_CANONICAL_LEGACY_REVIEW_REQUIRED")
    if (
        connection.scalar(sa.text("SELECT to_regclass('audit_schema.projects')"))
        is None
    ):
        raise RuntimeError("MANAGEMENT_CANONICAL_CORE_REQUIRED")
    # Structural dependency only. Core migration/tenant acceptance is separate.
    columns = {
        item["name"]: item
        for item in sa.inspect(connection).get_columns(
            "projects", schema="audit_schema"
        )
    }
    if (
        not {"id", "owner_id", "workspace_id", "status", "origin", "path_prefix"}
        <= columns.keys()
    ):
        raise RuntimeError("MANAGEMENT_CANONICAL_CORE_REQUIRED")
    key = columns["id"]["type"] if "id" in columns else None
    if not isinstance(key, sa.String) or key.length != 36:
        raise RuntimeError("MANAGEMENT_CANONICAL_CORE_KEY_INVALID")
    for name in TABLES:
        for fk in sa.inspect(connection).get_foreign_keys(name, schema="public"):
            if fk["constrained_columns"] == ["project_id"]:
                if fk["referred_table"] != "projects" or fk["referred_schema"] not in {
                    None,
                    "public",
                }:
                    raise RuntimeError("MANAGEMENT_CANONICAL_FOREIGN_KEY_DRIFT")
                constraint_name = fk["name"]
                if constraint_name is None:
                    raise RuntimeError("MANAGEMENT_CANONICAL_FOREIGN_KEY_DRIFT")
                op.drop_constraint(
                    constraint_name, name, type_="foreignkey", schema="public"
                )
        op.add_column(
            name,
            sa.Column(
                "canonical_project_id",
                sa.String(36),
                sa.Computed("project_id::text", persisted=True),
                nullable=False,
            ),
            schema="public",
        )
        op.create_foreign_key(
            "fk_" + name + "_canonical_project",
            name,
            "projects",
            ["canonical_project_id"],
            ["id"],
            source_schema="public",
            referent_schema="audit_schema",
            ondelete="RESTRICT",
        )
    # Archive legacy project storage; the new path cannot create a shadow project.
    op.execute(
        """CREATE FUNCTION public.reject_legacy_management_project_write() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN RAISE EXCEPTION 'MANAGEMENT_LEGACY_PROJECT_READ_ONLY'; END; $$"""
    )
    op.execute(
        """CREATE TRIGGER management_legacy_project_read_only BEFORE INSERT OR UPDATE OR DELETE ON public.projects
    FOR EACH ROW EXECUTE FUNCTION public.reject_legacy_management_project_write()"""
    )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text(
            "LOCK TABLE public.projects, public.tasks, public.hitl_approvals, "
            "public.management_changelog, public.hitl_history, public.management_outbox "
            "IN ACCESS EXCLUSIVE MODE"
        )
    )
    for name in (*TABLES, "management_outbox"):
        if connection.scalar(
            sa.text("SELECT EXISTS(SELECT 1 FROM public." + name + ")")
        ):
            raise RuntimeError("MANAGEMENT_CANONICAL_DOWNGRADE_WOULD_LOSE_BINDING")
    op.execute("DROP TRIGGER management_legacy_project_read_only ON public.projects")
    op.execute("DROP FUNCTION public.reject_legacy_management_project_write()")
    for name in reversed(TABLES):
        op.drop_constraint(
            "fk_" + name + "_canonical_project",
            name,
            type_="foreignkey",
            schema="public",
        )
        op.drop_column(name, "canonical_project_id", schema="public")
        if name != "hitl_history":
            op.create_foreign_key(
                name + "_project_id_fkey",
                name,
                "projects",
                ["project_id"],
                ["id"],
                source_schema="public",
                referent_schema="public",
                ondelete="CASCADE",
            )
