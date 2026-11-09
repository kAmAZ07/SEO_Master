"""Immutable DeploymentOperation records in the existing durable changelog."""
from alembic import op

revision = "20261109_003_execution"
down_revision = "20261109_002_canonical"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""CREATE UNIQUE INDEX uq_hitl_execution_version ON public.management_changelog
    (task_id, ((metadata->'record')->>'recommendation_version_id'))
    WHERE source='hitl_executor_v1' AND metadata->>'state' != 'cancelled'""")
    op.execute("""ALTER TABLE public.management_changelog ADD CONSTRAINT ck_hitl_execution_record CHECK
    (source != 'hitl_executor_v1' OR ((entity_type='deployment_operation' AND entity_id=id::text
    AND task_id IS NOT NULL AND metadata->'record'->>'operation_id'=id::text
    AND metadata->'record'->>'task_id'=task_id::text AND metadata->'record'->>'project_id'=project_id::text
    AND metadata->'record'->>'recommendation_version_id' IS NOT NULL
    AND metadata->>'state' IN ('ready','applying','outcome_unknown','applied_unverified','no_op','cancelled')
    AND metadata->>'record_hash' ~ '^[0-9a-f]{64}$') IS TRUE))""")
    op.execute("""CREATE FUNCTION public.protect_hitl_execution_record() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF OLD.source='hitl_executor_v1' THEN
        IF TG_OP='DELETE' THEN RAISE EXCEPTION 'HITL_EXECUTION_IMMUTABLE'; END IF;
        IF NEW.id IS DISTINCT FROM OLD.id OR NEW.task_id IS DISTINCT FROM OLD.task_id
          OR NEW.project_id IS DISTINCT FROM OLD.project_id OR NEW.entity_id IS DISTINCT FROM OLD.entity_id
          OR NEW.entity_type IS DISTINCT FROM OLD.entity_type OR NEW.change_type IS DISTINCT FROM OLD.change_type
          OR NEW.source IS DISTINCT FROM OLD.source OR NEW.before_value IS DISTINCT FROM OLD.before_value
          OR NEW.after_value IS DISTINCT FROM OLD.after_value OR NEW.created_at IS DISTINCT FROM OLD.created_at
          OR NEW.metadata->'record' IS DISTINCT FROM OLD.metadata->'record'
          OR NEW.metadata->'record_hash' IS DISTINCT FROM OLD.metadata->'record_hash' THEN
          RAISE EXCEPTION 'HITL_EXECUTION_IMMUTABLE';
        END IF;
        IF (OLD.metadata->>'state'='ready' AND NEW.metadata->>'state' NOT IN ('ready','applying','cancelled'))
          OR (OLD.metadata->>'state'='applying' AND NEW.metadata->>'state' NOT IN ('applying','outcome_unknown','applied_unverified','no_op'))
          OR (OLD.metadata->>'state'='outcome_unknown' AND NEW.metadata->>'state' NOT IN ('outcome_unknown','applied_unverified','no_op'))
          OR (OLD.metadata->>'state' IN ('applied_unverified','no_op','cancelled') AND
            (NEW.metadata IS DISTINCT FROM OLD.metadata OR NEW.applied IS DISTINCT FROM OLD.applied)) THEN
          RAISE EXCEPTION 'HITL_EXECUTION_INVALID_TRANSITION';
        END IF;
      ELSIF TG_OP='UPDATE' AND NEW.source='hitl_executor_v1' THEN
        RAISE EXCEPTION 'HITL_EXECUTION_IMMUTABLE';
      END IF;
      IF TG_OP='DELETE' THEN RETURN OLD; END IF;
      RETURN NEW;
    END; $$""")
    op.execute("""CREATE TRIGGER hitl_execution_immutable BEFORE UPDATE OR DELETE ON public.management_changelog
    FOR EACH ROW EXECUTE FUNCTION public.protect_hitl_execution_record()""")


def downgrade() -> None:
    from sqlalchemy import text
    if op.get_bind().scalar(text("SELECT EXISTS(SELECT 1 FROM public.management_changelog WHERE source='hitl_executor_v1')")):
        raise RuntimeError("HITL_EXECUTION_DOWNGRADE_WOULD_LOSE_DATA")
    op.execute("DROP TRIGGER hitl_execution_immutable ON public.management_changelog")
    op.execute("DROP FUNCTION public.protect_hitl_execution_record()")
    op.execute("ALTER TABLE public.management_changelog DROP CONSTRAINT ck_hitl_execution_record")
    op.execute("DROP INDEX public.uq_hitl_execution_version")
