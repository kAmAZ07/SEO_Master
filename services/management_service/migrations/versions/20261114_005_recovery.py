"""Bounded durable reconciliation journal; no command replacement or retry reset."""
from alembic import op
from sqlalchemy import text

revision = "20261114_005_recovery"
down_revision = "20261109_004_client_storage"
branch_labels = None
depends_on = None

JOURNAL_GUARD = """CREATE FUNCTION public.protect_hitl_recovery_journal() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE r jsonb; previous jsonb; a jsonb; old_a jsonb; i integer; n integer; old_n integer;
BEGIN
  IF NEW.source != 'hitl_executor_v1' THEN RETURN NEW; END IF;
  r := NEW.metadata->'recovery';
  IF r IS NULL THEN
    IF NEW.metadata->>'state' IN ('applying','outcome_unknown','needs_reconciliation') THEN
      RAISE EXCEPTION 'HITL_RECOVERY_REQUIRED';
    END IF;
    IF TG_OP='UPDATE' AND OLD.metadata ? 'recovery' THEN RAISE EXCEPTION 'HITL_RECOVERY_IMMUTABLE'; END IF;
    RETURN NEW;
  END IF;
  IF NEW.metadata->>'state' IN ('ready','cancelled') OR jsonb_typeof(r) != 'object'
    OR NOT r ?& ARRAY['version','started_at','deadline_at','next_attempt_at','attempts','last_error','stop_reason']
    OR r - ARRAY['version','started_at','deadline_at','next_attempt_at','attempts','last_error','stop_reason'] != '{}'::jsonb
    OR r->'version' != '1'::jsonb OR jsonb_typeof(r->'attempts') != 'array'
    OR (r->>'deadline_at')::timestamptz - (r->>'started_at')::timestamptz != interval '900 seconds'
    OR r->>'started_at' IS NULL OR r->>'deadline_at' IS NULL
    OR (r->>'stop_reason' IS NOT NULL AND r->>'stop_reason' NOT IN ('attempts_exhausted','deadline_exceeded'))
    OR (NEW.metadata->>'state'='needs_reconciliation' AND (r->>'stop_reason' IS NULL OR r->>'next_attempt_at' IS NOT NULL)) THEN
    RAISE EXCEPTION 'HITL_RECOVERY_INVALID';
  END IF;
  n := jsonb_array_length(r->'attempts');
  IF n > 4 THEN RAISE EXCEPTION 'HITL_RECOVERY_BUDGET'; END IF;
  FOR i IN 0..n-1 LOOP
    a := r->'attempts'->i;
    IF jsonb_typeof(a) != 'object'
      OR NOT a ?& ARRAY['number','lease_id','started_at','lease_until','status','completed_at','error_code']
      OR a - ARRAY['number','lease_id','started_at','lease_until','status','completed_at','error_code'] != '{}'::jsonb
      OR a->'number' != to_jsonb(i+1)
      OR (a->>'lease_id' ~ '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$') IS NOT TRUE
      OR a->>'started_at' IS NULL OR a->>'lease_until' IS NULL
      OR (a->>'lease_until')::timestamptz - (a->>'started_at')::timestamptz != interval '60 seconds'
      OR (a->>'started_at')::timestamptz < (r->>'started_at')::timestamptz
      OR (a->>'status' IN ('pending','failed','lease_expired','succeeded','superseded','abandoned')) IS NOT TRUE
      OR ((a->>'status'='pending') != (a->>'completed_at' IS NULL))
      OR (a->>'status'='pending' AND i != n-1)
      OR (a->>'completed_at')::timestamptz < (a->>'started_at')::timestamptz
      OR (a->>'status'='failed' AND (a->>'error_code' IN ('cms_unavailable','operation_not_found','receipt_invalid','integration_changed')) IS NOT TRUE)
      OR (a->>'status'='lease_expired' AND (a->>'error_code'='lease_expired') IS NOT TRUE)
      OR (a->>'status' IN ('pending','succeeded','superseded','abandoned') AND a->>'error_code' IS NOT NULL) THEN
      RAISE EXCEPTION 'HITL_RECOVERY_INVALID';
    END IF;
  END LOOP;
  IF TG_OP='UPDATE' AND OLD.metadata ? 'recovery' THEN
    previous := OLD.metadata->'recovery';
    old_n := jsonb_array_length(previous->'attempts');
    IF (r->>'started_at')::timestamptz != (previous->>'started_at')::timestamptz
      OR (r->>'deadline_at')::timestamptz != (previous->>'deadline_at')::timestamptz
      OR n < old_n OR n > old_n+1
      OR (previous->>'stop_reason' IS NOT NULL AND r->>'stop_reason' IS DISTINCT FROM previous->>'stop_reason') THEN
      RAISE EXCEPTION 'HITL_RECOVERY_IMMUTABLE';
    END IF;
    FOR i IN 0..old_n-1 LOOP
      a := r->'attempts'->i; old_a := previous->'attempts'->i;
      IF (old_a->>'status' != 'pending' AND a IS DISTINCT FROM old_a)
        OR (old_a->>'status'='pending' AND (
          a - ARRAY['status','completed_at','error_code'] IS DISTINCT FROM old_a - ARRAY['status','completed_at','error_code']
          OR (a->>'status'='pending' AND a IS DISTINCT FROM old_a))) THEN
        RAISE EXCEPTION 'HITL_RECOVERY_IMMUTABLE';
      END IF;
    END LOOP;
  ELSIF n != 0 THEN RAISE EXCEPTION 'HITL_RECOVERY_INVALID';
  END IF;
  RETURN NEW;
END; $$"""


def upgrade() -> None:
    op.execute("SET LOCAL TIME ZONE 'UTC'")
    op.execute("ALTER TABLE public.management_changelog DROP CONSTRAINT ck_hitl_execution_record")
    op.execute(NEW_CHECK)
    op.execute(NEW_GUARD)
    # Backfill only previously begun operations. Original clocks, commands and hashes
    # stay unchanged; old operations will reach manual reconciliation, not a fresh budget.
    op.execute("""UPDATE public.management_changelog SET metadata=metadata || jsonb_build_object('recovery',
      jsonb_build_object('version',1,'started_at',metadata->>'began_at',
        'deadline_at',((metadata->>'began_at')::timestamptz + interval '900 seconds'),
        'next_attempt_at',CASE WHEN metadata->>'state'='outcome_unknown' THEN (metadata->>'began_at')::timestamptz
          ELSE to_timestamp((metadata->'record'->'command'->>'authorization_deadline')::bigint + 10) END,
        'attempts','[]'::jsonb,'last_error',NULL,'stop_reason',NULL))
      WHERE source='hitl_executor_v1' AND metadata->>'state' IN ('applying','outcome_unknown')""")
    op.execute(JOURNAL_GUARD)
    op.execute("""CREATE TRIGGER hitl_execution_recovery_guard BEFORE INSERT OR UPDATE ON public.management_changelog
      FOR EACH ROW EXECUTE FUNCTION public.protect_hitl_recovery_journal()""")


def downgrade() -> None:
    # Serialize the emptiness check with begin/claim/report, not just later DDL.
    # Otherwise a concurrent journal could appear after EXISTS and be reset on upgrade.
    op.execute("LOCK TABLE public.management_changelog IN ACCESS EXCLUSIVE MODE")
    if op.get_bind().scalar(text("SELECT EXISTS(SELECT 1 FROM public.management_changelog WHERE source='hitl_executor_v1' AND metadata ? 'recovery')")):
        raise RuntimeError("HITL_RECOVERY_DOWNGRADE_WOULD_RESET_BUDGET")
    op.execute("DROP TRIGGER hitl_execution_recovery_guard ON public.management_changelog")
    op.execute("DROP FUNCTION public.protect_hitl_recovery_journal()")
    op.execute("ALTER TABLE public.management_changelog DROP CONSTRAINT ck_hitl_execution_record")
    op.execute(OLD_CHECK)
    op.execute(OLD_GUARD)

OLD_CHECK = """ALTER TABLE public.management_changelog ADD CONSTRAINT ck_hitl_execution_record CHECK
    (source != 'hitl_executor_v1' OR ((entity_type='deployment_operation' AND entity_id=id::text
    AND task_id IS NOT NULL AND metadata->'record'->>'operation_id'=id::text
    AND metadata->'record'->>'task_id'=task_id::text AND metadata->'record'->>'project_id'=project_id::text
    AND metadata->'record'->>'recommendation_version_id' IS NOT NULL
    AND metadata->>'state' IN ('ready','applying','outcome_unknown','applied_unverified','no_op','cancelled')
    AND metadata->>'record_hash' ~ '^[0-9a-f]{64}$') IS TRUE))"""
OLD_GUARD = """CREATE OR REPLACE FUNCTION public.protect_hitl_execution_record() RETURNS trigger LANGUAGE plpgsql AS $$
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
    END; $$"""
NEW_CHECK = """ALTER TABLE public.management_changelog ADD CONSTRAINT ck_hitl_execution_record CHECK
    (source != 'hitl_executor_v1' OR ((entity_type='deployment_operation' AND entity_id=id::text
    AND task_id IS NOT NULL AND metadata->'record'->>'operation_id'=id::text
    AND metadata->'record'->>'task_id'=task_id::text AND metadata->'record'->>'project_id'=project_id::text
    AND metadata->'record'->>'recommendation_version_id' IS NOT NULL
    AND metadata->>'state' IN ('ready','applying','outcome_unknown','needs_reconciliation','applied_unverified','no_op','cancelled')
    AND metadata->>'record_hash' ~ '^[0-9a-f]{64}$') IS TRUE))"""
NEW_GUARD = """CREATE OR REPLACE FUNCTION public.protect_hitl_execution_record() RETURNS trigger LANGUAGE plpgsql AS $$
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
          OR (OLD.metadata->>'state'='applying' AND NEW.metadata->>'state' NOT IN ('applying','outcome_unknown','needs_reconciliation','applied_unverified','no_op'))
          OR (OLD.metadata->>'state'='outcome_unknown' AND NEW.metadata->>'state' NOT IN ('outcome_unknown','needs_reconciliation','applied_unverified','no_op'))
          OR (OLD.metadata->>'state'='needs_reconciliation' AND (NEW.metadata->>'state' NOT IN ('needs_reconciliation','applied_unverified','no_op') OR (NEW.metadata->>'state'='needs_reconciliation' AND NEW.metadata IS DISTINCT FROM OLD.metadata)))
          OR (OLD.metadata->>'state' IN ('applied_unverified','no_op','cancelled') AND
            (NEW.metadata IS DISTINCT FROM OLD.metadata OR NEW.applied IS DISTINCT FROM OLD.applied)) THEN
          RAISE EXCEPTION 'HITL_EXECUTION_INVALID_TRANSITION';
        END IF;
      ELSIF TG_OP='UPDATE' AND NEW.source='hitl_executor_v1' THEN
        RAISE EXCEPTION 'HITL_EXECUTION_IMMUTABLE';
      END IF;
      IF TG_OP='DELETE' THEN RETURN OLD; END IF;
      RETURN NEW;
    END; $$"""
