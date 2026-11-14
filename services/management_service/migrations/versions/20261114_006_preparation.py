"""Pre-dispatch intent and leased preparation in the existing operation row."""
from alembic import op
from sqlalchemy import text

revision = "20261114_006_preparation"
down_revision = "20261114_005_recovery"
branch_labels = None
depends_on = None

RECORD_CHECK = """ALTER TABLE public.management_changelog ADD CONSTRAINT ck_hitl_execution_record CHECK
(source != 'hitl_executor_v1' OR ((entity_type='deployment_operation' AND entity_id=id::text
 AND task_id IS NOT NULL AND metadata->'record'->>'operation_id'=id::text
 AND metadata->'record'->>'task_id'=task_id::text AND metadata->'record'->>'project_id'=project_id::text
 AND metadata->'record'->>'recommendation_version_id' IS NOT NULL
 AND metadata->>'state' IN ('queued','preflight','dry_run','ready','rejected','unsupported','conflict','failed_before_apply',
   'applying','outcome_unknown','needs_reconciliation','applied_unverified','no_op','cancelled')
 AND metadata->>'record_hash' ~ '^[0-9a-f]{64}$'
 AND (NOT metadata ? 'intent' OR (
   jsonb_typeof(metadata->'intent')='object' AND metadata->>'intent_hash' ~ '^[0-9a-f]{64}$'
   AND metadata->'intent' ?& ARRAY['operation_id','task_id','approval_id','recommendation_version_id','content_hash',
      'project_id','workspace_id','actor_id','actor_role','decision_hash','version']
   AND (CASE WHEN metadata->'prepared'='false'::jsonb THEN
     metadata->'record'=metadata->'intent' AND before_value IS NULL AND after_value IS NULL
     AND metadata->>'state' IN ('queued','preflight','rejected','unsupported','conflict','failed_before_apply','cancelled')
   WHEN metadata->'prepared'='true'::jsonb THEN
     (metadata->'record') - ARRAY['command','request_hash','binding','before','after'] = metadata->'intent'
     AND jsonb_typeof(before_value)='object' AND jsonb_typeof(after_value)='object'
     AND before_value=metadata->'record'->'before' AND after_value=metadata->'record'->'after'
     AND jsonb_typeof(metadata->'record'->'command')='object' AND metadata->'record'->>'request_hash' ~ '^[0-9a-f]{64}$'
   ELSE false END)))
 AND (metadata->>'state' NOT IN ('queued','preflight','dry_run','rejected','unsupported','conflict','failed_before_apply') OR metadata ? 'preparation')
) IS TRUE))"""

RECORD_GUARD = """CREATE OR REPLACE FUNCTION public.protect_hitl_execution_record() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE freezing boolean; transitions jsonb := '{
 "queued":["queued","preflight","rejected","unsupported","conflict","failed_before_apply","cancelled"],
 "preflight":["preflight","dry_run","rejected","unsupported","conflict","failed_before_apply","cancelled"],
 "dry_run":["dry_run","preflight","ready","rejected","unsupported","conflict","failed_before_apply","cancelled"],
 "ready":["ready","preflight","applying","rejected","unsupported","conflict","failed_before_apply","cancelled"],
 "rejected":["rejected","cancelled"],
 "unsupported":["unsupported","cancelled"],"conflict":["conflict","cancelled"],"failed_before_apply":["failed_before_apply","cancelled"],
 "applying":["applying","outcome_unknown","needs_reconciliation","applied_unverified","no_op"],
 "outcome_unknown":["outcome_unknown","needs_reconciliation","applied_unverified","no_op"],
 "needs_reconciliation":["needs_reconciliation","applied_unverified","no_op"],
 "applied_unverified":["applied_unverified"],"no_op":["no_op"],"cancelled":["cancelled"]}'::jsonb;
BEGIN
 IF OLD.source='hitl_executor_v1' THEN
  IF TG_OP='DELETE' THEN RAISE EXCEPTION 'HITL_EXECUTION_IMMUTABLE'; END IF;
  freezing := (OLD.metadata->'prepared'='false'::jsonb AND NEW.metadata->'prepared'='true'::jsonb
    AND OLD.metadata->>'state'='preflight' AND NEW.metadata->>'state'='dry_run'
    AND OLD.metadata->'record'=OLD.metadata->'intent'
    AND (NEW.metadata->'record') - ARRAY['command','request_hash','binding','before','after']=OLD.metadata->'intent') IS TRUE;
  IF NEW.id IS DISTINCT FROM OLD.id OR NEW.task_id IS DISTINCT FROM OLD.task_id
    OR NEW.project_id IS DISTINCT FROM OLD.project_id OR NEW.entity_id IS DISTINCT FROM OLD.entity_id
    OR NEW.entity_type IS DISTINCT FROM OLD.entity_type OR NEW.change_type IS DISTINCT FROM OLD.change_type
    OR NEW.source IS DISTINCT FROM OLD.source OR NEW.created_at IS DISTINCT FROM OLD.created_at
    OR NEW.metadata->'intent' IS DISTINCT FROM OLD.metadata->'intent'
    OR NEW.metadata->'intent_hash' IS DISTINCT FROM OLD.metadata->'intent_hash'
    OR (NOT freezing AND (NEW.before_value IS DISTINCT FROM OLD.before_value OR NEW.after_value IS DISTINCT FROM OLD.after_value
       OR NEW.metadata->'record' IS DISTINCT FROM OLD.metadata->'record'
       OR NEW.metadata->'record_hash' IS DISTINCT FROM OLD.metadata->'record_hash'
       OR NEW.metadata->'prepared' IS DISTINCT FROM OLD.metadata->'prepared')) THEN
    RAISE EXCEPTION 'HITL_EXECUTION_IMMUTABLE';
  END IF;
  IF (transitions->(OLD.metadata->>'state') ? (NEW.metadata->>'state')) IS NOT TRUE
    OR (OLD.metadata->>'state' IN ('applied_unverified','no_op','cancelled','needs_reconciliation','rejected','unsupported','conflict','failed_before_apply')
      AND NEW.metadata->>'state'=OLD.metadata->>'state' AND
      (NEW.metadata IS DISTINCT FROM OLD.metadata OR NEW.applied IS DISTINCT FROM OLD.applied)) THEN
    RAISE EXCEPTION 'HITL_EXECUTION_INVALID_TRANSITION';
  END IF;
 ELSIF TG_OP='UPDATE' AND NEW.source='hitl_executor_v1' THEN RAISE EXCEPTION 'HITL_EXECUTION_IMMUTABLE';
 END IF;
 IF TG_OP='DELETE' THEN RETURN OLD; END IF;
 RETURN NEW;
END; $$"""

PREPARATION_GUARD = """CREATE FUNCTION public.protect_hitl_preparation() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE p jsonb; old_p jsonb; e jsonb; old_n integer; n integer; i integer; attempt integer; previous_at timestamptz;
BEGIN
 IF NEW.source!='hitl_executor_v1' THEN RETURN NEW; END IF;
 p := NEW.metadata->'preparation';
 IF TG_OP='INSERT' AND (NEW.metadata->>'state'!='queued' OR NEW.metadata->'prepared' IS DISTINCT FROM 'false'::jsonb OR NOT NEW.metadata ? 'intent') THEN
   RAISE EXCEPTION 'HITL_PREPARATION_QUEUE_REQUIRED';
 END IF;
 IF p IS NULL THEN
   IF NEW.metadata ? 'intent' OR (TG_OP='UPDATE' AND OLD.metadata ? 'preparation') THEN RAISE EXCEPTION 'HITL_PREPARATION_REQUIRED'; END IF;
   RETURN NEW;
 END IF;
 IF jsonb_typeof(p)!='object' OR p->'version' IS DISTINCT FROM '1'::jsonb
   OR NOT p ?& ARRAY['version','started_at','deadline_at','attempts','lease_id','lease_until','events']
   OR p - ARRAY['version','started_at','deadline_at','attempts','lease_id','lease_until','events']!='{}'::jsonb
   OR p->>'started_at' IS NULL OR p->>'deadline_at' IS NULL
   OR (p->>'deadline_at')::timestamptz-(p->>'started_at')::timestamptz != interval '900 seconds'
   OR (p->>'started_at' ~ '(Z|[+]00:00)$') IS NOT TRUE OR (p->>'deadline_at' ~ '(Z|[+]00:00)$') IS NOT TRUE
   OR jsonb_typeof(p->'attempts')!='number' OR (p->>'attempts' ~ '^[0-4]$') IS NOT TRUE
   OR jsonb_typeof(p->'events')!='array' THEN RAISE EXCEPTION 'HITL_PREPARATION_INVALID';
 END IF;
 attempt := (p->>'attempts')::integer; n := jsonb_array_length(p->'events');
 IF n<1 OR n>20 OR ((attempt=0) != (p->>'lease_id' IS NULL)) OR ((p->>'lease_id' IS NULL) != (p->>'lease_until' IS NULL))
   OR (attempt>0 AND ((p->>'lease_id' ~ '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$') IS NOT TRUE
      OR (p->>'lease_until')::timestamptz>(p->>'deadline_at')::timestamptz)) THEN RAISE EXCEPTION 'HITL_PREPARATION_INVALID'; END IF;
 previous_at := (p->>'started_at')::timestamptz;
 FOR i IN 0..n-1 LOOP
  e := p->'events'->i;
  IF jsonb_typeof(e)!='object' OR NOT e ?& ARRAY['phase','at','attempt','error_code']
    OR e - ARRAY['phase','at','attempt','error_code']!='{}'::jsonb
    OR (e->>'phase' IN ('queued','preflight','dry_run','ready','rejected','unsupported','conflict','failed_before_apply','cancelled')) IS NOT TRUE
    OR (e->>'at' ~ '(Z|[+]00:00)$') IS NOT TRUE OR (e->>'at')::timestamptz < previous_at
    OR jsonb_typeof(e->'attempt')!='number' OR (e->>'attempt' ~ '^[0-4]$') IS NOT TRUE OR (e->>'attempt')::integer>attempt
    OR ((e->>'phase' IN ('rejected','unsupported','conflict','failed_before_apply')) != (e->>'error_code' IS NOT NULL))
    OR (e->>'error_code' IS NOT NULL AND e->>'error_code' NOT IN ('cms_unavailable','cms_precondition_conflict','cms_unsupported','cms_contract_invalid',
       'integration_changed','authority_unavailable','authorization_expired','authorization_rejected','preparation_expired')) THEN RAISE EXCEPTION 'HITL_PREPARATION_INVALID'; END IF;
  previous_at := (e->>'at')::timestamptz;
 END LOOP;
 IF (p->'events'->0->>'attempt')::integer!=0 OR p->'events'->0->>'phase' NOT IN ('queued','ready')
   OR (NEW.metadata->>'state' IN ('queued','preflight','dry_run','ready','rejected','unsupported','conflict','failed_before_apply','cancelled')
       AND NEW.metadata->>'state' IS DISTINCT FROM p->'events'->(n-1)->>'phase') THEN RAISE EXCEPTION 'HITL_PREPARATION_INVALID'; END IF;
 IF TG_OP='UPDATE' AND OLD.metadata ? 'preparation' THEN
  old_p := OLD.metadata->'preparation'; old_n := jsonb_array_length(old_p->'events');
  IF p->'started_at' IS DISTINCT FROM old_p->'started_at' OR p->'deadline_at' IS DISTINCT FROM old_p->'deadline_at'
    OR attempt<(old_p->>'attempts')::integer OR attempt>(old_p->>'attempts')::integer+1 OR n<old_n OR n>old_n+1 THEN
    RAISE EXCEPTION 'HITL_PREPARATION_IMMUTABLE'; END IF;
  FOR i IN 0..old_n-1 LOOP
   IF p->'events'->i IS DISTINCT FROM old_p->'events'->i THEN RAISE EXCEPTION 'HITL_PREPARATION_IMMUTABLE'; END IF;
  END LOOP;
  IF attempt=(old_p->>'attempts')::integer THEN
   IF p->'lease_id' IS DISTINCT FROM old_p->'lease_id' OR p->'lease_until' IS DISTINCT FROM old_p->'lease_until' THEN
     RAISE EXCEPTION 'HITL_PREPARATION_IMMUTABLE'; END IF;
  ELSIF NEW.metadata->>'state'!='preflight' OR p->'lease_id' IS NOT DISTINCT FROM old_p->'lease_id'
    OR (p->'events'->(n-1)->>'at')::timestamptz < (old_p->>'lease_until')::timestamptz THEN
    RAISE EXCEPTION 'HITL_PREPARATION_LEASE_CONFLICT';
  END IF;
 ELSIF TG_OP='INSERT' THEN
  IF attempt!=0 OR n!=1 THEN RAISE EXCEPTION 'HITL_PREPARATION_QUEUE_REQUIRED'; END IF;
 ELSIF OLD.metadata->>'state'!='ready' OR n>2 THEN RAISE EXCEPTION 'HITL_PREPARATION_INVALID';
 END IF;
 RETURN NEW;
END; $$"""


def upgrade() -> None:
    op.execute("ALTER TABLE public.management_changelog DROP CONSTRAINT ck_hitl_execution_record")
    op.execute(RECORD_CHECK)
    op.execute(RECORD_GUARD)
    op.execute(PREPARATION_GUARD)
    op.execute("""CREATE TRIGGER hitl_execution_preparation_guard BEFORE INSERT OR UPDATE ON public.management_changelog
      FOR EACH ROW EXECUTE FUNCTION public.protect_hitl_preparation()""")


def downgrade() -> None:
    op.execute("LOCK TABLE public.management_changelog IN ACCESS EXCLUSIVE MODE")
    if op.get_bind().scalar(text("SELECT EXISTS(SELECT 1 FROM public.management_changelog WHERE source='hitl_executor_v1' AND (metadata ? 'intent' OR metadata ? 'preparation'))")):
        raise RuntimeError("HITL_PREPARATION_DOWNGRADE_WOULD_LOSE_HISTORY")
    op.execute("DROP TRIGGER hitl_execution_preparation_guard ON public.management_changelog")
    op.execute("DROP FUNCTION public.protect_hitl_preparation()")
    op.execute("ALTER TABLE public.management_changelog DROP CONSTRAINT ck_hitl_execution_record")
    op.execute(PREVIOUS_CHECK)
    op.execute(PREVIOUS_GUARD)

PREVIOUS_CHECK = """ALTER TABLE public.management_changelog ADD CONSTRAINT ck_hitl_execution_record CHECK
    (source != 'hitl_executor_v1' OR ((entity_type='deployment_operation' AND entity_id=id::text
    AND task_id IS NOT NULL AND metadata->'record'->>'operation_id'=id::text
    AND metadata->'record'->>'task_id'=task_id::text AND metadata->'record'->>'project_id'=project_id::text
    AND metadata->'record'->>'recommendation_version_id' IS NOT NULL
    AND metadata->>'state' IN ('ready','applying','outcome_unknown','needs_reconciliation','applied_unverified','no_op','cancelled')
    AND metadata->>'record_hash' ~ '^[0-9a-f]{64}$') IS TRUE))"""
PREVIOUS_GUARD = """CREATE OR REPLACE FUNCTION public.protect_hitl_execution_record() RETURNS trigger LANGUAGE plpgsql AS $$
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
