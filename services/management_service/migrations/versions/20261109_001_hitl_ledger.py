"""AR-4 additive ledger revision for the future Management migration line.

Requires the existing public tasks/hitl_approvals baseline. Deliberately separate
from the Core revision graph: AR-3 must supply its runner/version table/grants.
Native tests invoke these exact operations on disposable databases only.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "20261109_001_hitl_ledger"
down_revision = "20261109_000_management_base"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("tasks", "hitl_approvals"):
        if op.get_bind().scalar(sa.text("SELECT to_regclass(:name)"), {"name": "public." + table}) is None:
            raise RuntimeError("AR4_MANAGEMENT_BASELINE_REQUIRED")
    op.create_table(
        "hitl_history",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("approval_id", UUID(as_uuid=True), sa.ForeignKey("public.hitl_approvals.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("task_id", UUID(as_uuid=True), sa.ForeignKey("public.tasks.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("project_id", UUID(as_uuid=True), nullable=False),
        sa.Column("version_id", sa.String(36), nullable=False),
        sa.Column("entry_type", sa.String(16), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("entry_hash", sa.String(64), nullable=False),
        sa.Column("metadata", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("approval_id", "version_id", "entry_type", name="uq_hitl_history_entry"),
        sa.CheckConstraint("entry_type IN ('prepared','approve','reject','revoke')", name="ck_hitl_history_type"),
        sa.CheckConstraint("content_hash ~ '^[0-9a-f]{64}$' AND entry_hash ~ '^[0-9a-f]{64}$'", name="ck_hitl_history_hash"),
        schema="public",
    )
    op.create_index("idx_hitl_history_task_created", "hitl_history", ["task_id", "created_at", "id"], schema="public")
    op.create_table(
        "management_outbox",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("history_id", UUID(as_uuid=True), sa.ForeignKey("public.hitl_history.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("event_type", sa.String(100), nullable=False),
        sa.Column("routing_key", sa.String(255), nullable=False),
        sa.Column("status", sa.String(16), server_default="pending", nullable=False),
        sa.Column("attempts", sa.Integer, server_default="0", nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("lease_token", UUID(as_uuid=True)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(64)),
        sa.Column("metadata", JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("history_id", name="uq_management_outbox_history"),
        sa.CheckConstraint("status IN ('pending','leased','published')", name="ck_management_outbox_status"),
        sa.CheckConstraint("attempts >= 0", name="ck_management_outbox_attempts"),
        sa.CheckConstraint("(status = 'published') = (published_at IS NOT NULL)", name="ck_management_outbox_published"),
        sa.CheckConstraint("(status = 'leased' AND lease_token IS NOT NULL AND lease_expires_at IS NOT NULL) OR "
                           "(status != 'leased' AND lease_token IS NULL AND lease_expires_at IS NULL)",
                           name="ck_management_outbox_lease"),
        schema="public",
    )
    op.create_index("idx_management_outbox_delivery", "management_outbox", ["status", "available_at", "lease_expires_at"], schema="public")
    op.execute("""CREATE FUNCTION public.reject_hitl_history_change() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN RAISE EXCEPTION 'HITL_HISTORY_IMMUTABLE'; END; $$""")
    op.execute("""CREATE TRIGGER hitl_history_immutable BEFORE UPDATE OR DELETE ON public.hitl_history
    FOR EACH ROW EXECUTE FUNCTION public.reject_hitl_history_change()""")
    op.execute("""CREATE FUNCTION public.protect_management_outbox_envelope() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF NEW.id IS DISTINCT FROM OLD.id OR NEW.history_id IS DISTINCT FROM OLD.history_id
        OR NEW.event_type IS DISTINCT FROM OLD.event_type OR NEW.routing_key IS DISTINCT FROM OLD.routing_key
        OR NEW.metadata IS DISTINCT FROM OLD.metadata OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION 'OUTBOX_ENVELOPE_IMMUTABLE';
      END IF;
      RETURN NEW;
    END; $$""")
    op.execute("""CREATE TRIGGER management_outbox_envelope_immutable BEFORE UPDATE ON public.management_outbox
    FOR EACH ROW EXECUTE FUNCTION public.protect_management_outbox_envelope()""")


def downgrade() -> None:
    if op.get_bind().scalar(sa.text("SELECT EXISTS(SELECT 1 FROM public.hitl_history) OR "
                                    "EXISTS(SELECT 1 FROM public.management_outbox)")):
        raise RuntimeError("AR4_LEDGER_DOWNGRADE_WOULD_LOSE_DATA")
    op.drop_table("management_outbox", schema="public")
    op.drop_table("hitl_history", schema="public")
    op.execute("DROP FUNCTION public.protect_management_outbox_envelope()")
    op.execute("DROP FUNCTION public.reject_hitl_history_change()")
