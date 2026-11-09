"""Producer commit barrier for AR-4, without granting Management producer DML."""
from alembic import op

revision = "20261109_001_hitl_barrier"
down_revision = "20261104_001_semantic_al5"
branch_labels = None
depends_on = None
TABLES = ("audit_schema.crawl_results", "semantic_schema.recommendation_versions", "public.users",
          "audit_schema.projects", "audit_schema.workspace_members", "audit_schema.domain_authorizations")
PARTITIONS = tuple(f"audit_schema.crawl_results_p{number}" for number in range(8))


def upgrade() -> None:
    op.execute("""CREATE FUNCTION public.hitl_producer_barrier() RETURNS trigger
    LANGUAGE plpgsql SET search_path=pg_catalog,pg_temp AS $$ BEGIN
      PERFORM pg_catalog.pg_advisory_xact_lock_shared(74219031109004);
      IF TG_LEVEL='ROW' THEN
        IF TG_OP='DELETE' THEN RETURN OLD; END IF;
        RETURN NEW;
      END IF;
      RETURN NULL;
    END; $$""")
    # Statement triggers acquire the barrier before any producer row/tuple change.
    for table in TABLES:
        op.execute("CREATE TRIGGER hitl_producer_barrier BEFORE INSERT OR UPDATE OR DELETE OR TRUNCATE ON "
                   + table + " FOR EACH STATEMENT EXECUTE FUNCTION public.hitl_producer_barrier()")
        # PostgreSQL clones this row trigger onto partitions, covering direct child DML too.
        op.execute("CREATE TRIGGER hitl_producer_row_barrier BEFORE INSERT OR UPDATE OR DELETE ON "
                   + table + " FOR EACH ROW EXECUTE FUNCTION public.hitl_producer_barrier()")
    for table in PARTITIONS:
        op.execute("CREATE TRIGGER hitl_producer_barrier BEFORE INSERT OR UPDATE OR DELETE OR TRUNCATE ON "
                   + table + " FOR EACH STATEMENT EXECUTE FUNCTION public.hitl_producer_barrier()")


def downgrade() -> None:
    for table in PARTITIONS:
        op.execute("DROP TRIGGER hitl_producer_barrier ON " + table)
    for table in TABLES:
        op.execute("DROP TRIGGER hitl_producer_row_barrier ON " + table)
        op.execute("DROP TRIGGER hitl_producer_barrier ON " + table)
    op.execute("DROP FUNCTION public.hitl_producer_barrier()")
