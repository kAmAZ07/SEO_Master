"""Public verification journal and explicit rollback in the existing operation ledger."""
from alembic import op
from sqlalchemy import text

revision = "20261114_007_verification"
down_revision = "20261114_006_preparation"
branch_labels = None
depends_on = None

JOURNAL_GUARD = """CREATE FUNCTION public.protect_hitl_verification() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v jsonb; old_v jsonb; a jsonb; old_a jsonb; b jsonb; i integer; n integer; old_n integer; kind text; original jsonb;
BEGIN
 IF NEW.source!='hitl_executor_v1' THEN RETURN NEW; END IF;
 kind := COALESCE(NEW.metadata->>'operation_type','apply');
 IF kind NOT IN ('apply','rollback') OR (kind='rollback' AND (
    NEW.metadata->'record'->>'operation_type' IS DISTINCT FROM 'rollback'
    OR NEW.metadata->'record'->>'original_operation_id' IS NULL
    OR NEW.metadata->'record'->>'reason' IS NULL)) THEN RAISE EXCEPTION 'HITL_OPERATION_TYPE_INVALID'; END IF;
 IF NEW.metadata ? 'public_baseline' THEN
  b := NEW.metadata->'public_baseline';
  IF jsonb_typeof(b)!='object' OR b->>'project_id' IS DISTINCT FROM NEW.project_id::text
    OR b->>'recommendation_version_id' IS DISTINCT FROM NEW.metadata->'record'->>'recommendation_version_id'
    OR b->>'content_hash' IS DISTINCT FROM NEW.metadata->'record'->>'content_hash'
    OR b->>'operation_id' IS DISTINCT FROM (CASE WHEN kind='rollback' THEN NEW.metadata->'record'->>'original_operation_id' ELSE NEW.id::text END)
    OR b->'observation'->>'url' IS DISTINCT FROM NEW.metadata->'record'->'version'->'target'->>'url'
    OR b->'observation'->>'final_url' IS DISTINCT FROM b->'observation'->>'url'
    OR b->'observation'->'status' IS DISTINCT FROM '200'::jsonb
    OR jsonb_typeof(b->'observation'->'facts') IS DISTINCT FROM 'object' THEN RAISE EXCEPTION 'HITL_BASELINE_INVALID'; END IF;
 END IF;
 IF TG_OP='UPDATE' THEN
  IF OLD.metadata ? 'public_baseline' AND NEW.metadata->'public_baseline' IS DISTINCT FROM OLD.metadata->'public_baseline' THEN
    RAISE EXCEPTION 'HITL_BASELINE_IMMUTABLE'; END IF;
  IF NOT OLD.metadata ? 'public_baseline' AND NEW.metadata ? 'public_baseline' AND
    (kind!='apply' OR OLD.metadata->>'state' NOT IN ('preflight','dry_run','ready')) THEN RAISE EXCEPTION 'HITL_BASELINE_TOO_LATE'; END IF;
  IF OLD.metadata ? 'rollback_operation_id' AND NEW.metadata->'rollback_operation_id' IS DISTINCT FROM OLD.metadata->'rollback_operation_id' THEN
    RAISE EXCEPTION 'HITL_ROLLBACK_IMMUTABLE'; END IF;
  IF NEW.metadata ? 'rollback_operation_id' AND NOT OLD.metadata ? 'rollback_operation_id' THEN
    SELECT metadata INTO original FROM public.management_changelog WHERE id=(NEW.metadata->>'rollback_operation_id')::uuid
      AND source='hitl_executor_v1' AND task_id=NEW.task_id AND project_id=NEW.project_id;
    IF kind!='apply' OR NEW.metadata->>'state'!='rollback_in_progress' OR original->>'state' IS DISTINCT FROM 'applying'
      OR original->>'operation_type' IS DISTINCT FROM 'rollback' OR original->'record'->>'original_operation_id' IS DISTINCT FROM NEW.id::text THEN
      RAISE EXCEPTION 'HITL_ROLLBACK_LINK_INVALID'; END IF;
  END IF;
  IF OLD.metadata ? 'receipt' AND (NEW.metadata->'receipt' IS DISTINCT FROM OLD.metadata->'receipt'
    OR NEW.applied IS DISTINCT FROM OLD.applied OR NEW.metadata->'finished_at' IS DISTINCT FROM OLD.metadata->'finished_at'
    OR NEW.metadata->'recovery' IS DISTINCT FROM OLD.metadata->'recovery'
    OR NEW.metadata->'preparation' IS DISTINCT FROM OLD.metadata->'preparation') THEN RAISE EXCEPTION 'HITL_RECEIPT_IMMUTABLE'; END IF;
 END IF;
 v := NEW.metadata->'verification';
 IF v IS NULL THEN
  IF (TG_OP='UPDATE' AND OLD.metadata ? 'verification') OR NEW.metadata->>'state' IN
    ('verifying','verified','verification_failed','rollback_verifying','rollback_verified','rollback_verification_failed') THEN
    RAISE EXCEPTION 'HITL_VERIFICATION_REQUIRED'; END IF;
  RETURN NEW;
 END IF;
 IF NOT NEW.metadata ? 'receipt' OR jsonb_typeof(v)!='object' OR v->'version' IS DISTINCT FROM '1'::jsonb
   OR NOT v ?& ARRAY['version','started_at','deadline_seconds','deadline_at','next_attempt_at','attempts','status','stop_reason']
   OR v - ARRAY['version','started_at','deadline_seconds','deadline_at','next_attempt_at','attempts','status','stop_reason']!='{}'::jsonb
   OR (v->>'deadline_seconds')::integer NOT BETWEEN 30 AND 3600
   OR (v->>'deadline_at')::timestamptz-(v->>'started_at')::timestamptz!=make_interval(secs=>(v->>'deadline_seconds')::integer)
   OR v->>'started_at' IS NULL OR v->>'deadline_at' IS NULL OR jsonb_typeof(v->'attempts')!='array'
   OR (v->>'status' IN ('pending','matched','failed','superseded')) IS NOT TRUE
   OR ((v->>'status'='pending') != (v->>'next_attempt_at' IS NOT NULL)) THEN RAISE EXCEPTION 'HITL_VERIFICATION_INVALID'; END IF;
 n := jsonb_array_length(v->'attempts');
 IF n>8 THEN RAISE EXCEPTION 'HITL_VERIFICATION_BUDGET'; END IF;
 FOR i IN 0..n-1 LOOP
  a := v->'attempts'->i;
  IF jsonb_typeof(a)!='object' OR a->'number' IS DISTINCT FROM to_jsonb(i+1)
    OR NOT a ?& ARRAY['number','lease_id','started_at','lease_until','completed_at','status','result']
    OR a - ARRAY['number','lease_id','started_at','lease_until','completed_at','status','result']!='{}'::jsonb
    OR (a->>'lease_id' ~ '^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$') IS NOT TRUE
    OR a->>'started_at' IS NULL OR a->>'lease_until' IS NULL
    OR (a->>'lease_until')::timestamptz-(a->>'started_at')::timestamptz!=interval '30 seconds'
    OR (a->>'started_at')::timestamptz<(v->>'started_at')::timestamptz
    OR (a->>'status' IN ('pending','observed','lease_expired','superseded')) IS NOT TRUE
    OR ((a->>'status'='pending') != (a->>'completed_at' IS NULL))
    OR ((a->>'status'='observed') != (jsonb_typeof(a->'result')='object'))
    OR (a->>'status'='pending' AND i!=n-1)
    OR (a->>'completed_at')::timestamptz<(a->>'started_at')::timestamptz THEN RAISE EXCEPTION 'HITL_VERIFICATION_INVALID'; END IF;
 END LOOP;
 IF TG_OP='UPDATE' AND OLD.metadata ? 'verification' THEN
  old_v := OLD.metadata->'verification'; old_n := jsonb_array_length(old_v->'attempts');
  IF v->'started_at' IS DISTINCT FROM old_v->'started_at' OR v->'deadline_at' IS DISTINCT FROM old_v->'deadline_at'
    OR v->'deadline_seconds' IS DISTINCT FROM old_v->'deadline_seconds' OR n<old_n OR n>old_n+1
    OR (old_v->>'status'!='pending' AND v IS DISTINCT FROM old_v AND v->>'status'!='superseded') THEN
    RAISE EXCEPTION 'HITL_VERIFICATION_IMMUTABLE'; END IF;
  FOR i IN 0..old_n-1 LOOP
   a := v->'attempts'->i; old_a := old_v->'attempts'->i;
   IF (old_a->>'status'!='pending' AND a IS DISTINCT FROM old_a) OR (old_a->>'status'='pending' AND (
      a - ARRAY['status','completed_at','result'] IS DISTINCT FROM old_a - ARRAY['status','completed_at','result']
      OR (a->>'status'='pending' AND a IS DISTINCT FROM old_a))) THEN RAISE EXCEPTION 'HITL_VERIFICATION_IMMUTABLE'; END IF;
  END LOOP;
 ELSIF n>1 THEN RAISE EXCEPTION 'HITL_VERIFICATION_INVALID';
 END IF;
 IF v->>'status'='matched' AND (n=0 OR v->'attempts'->(n-1)->>'status'!='observed'
    OR v->'attempts'->(n-1)->'result'->>'error_code' IS NOT NULL
    OR jsonb_array_length(v->'attempts'->(n-1)->'result'->'checks')=0
    OR EXISTS(SELECT 1 FROM jsonb_array_elements(v->'attempts'->(n-1)->'result'->'checks') c WHERE c->'matches' IS DISTINCT FROM 'true'::jsonb)) THEN
    RAISE EXCEPTION 'HITL_VERIFICATION_PROOF_REQUIRED'; END IF;
 IF (NEW.metadata->>'state' IN ('verified','rollback_verified') AND v->>'status'!='matched')
   OR (v->>'status'='superseded' AND (kind!='apply' OR NEW.metadata->>'state' NOT IN ('rollback_in_progress','rolled_back')))
   OR (NEW.metadata->>'state' IN ('verification_failed','rollback_verification_failed') AND v->>'status'!='failed') THEN
   RAISE EXCEPTION 'HITL_VERIFICATION_STATE_INVALID'; END IF;
 RETURN NEW;
END; $$"""


def upgrade() -> None:
    op.execute("LOCK TABLE public.management_changelog IN ACCESS EXCLUSIVE MODE")
    op.execute("ALTER TABLE public.management_changelog DROP CONSTRAINT ck_hitl_execution_record")
    op.execute(RECORD_CHECK)
    op.execute(RECORD_GUARD)
    op.execute("DROP INDEX public.uq_hitl_execution_version")
    op.execute("""CREATE UNIQUE INDEX uq_hitl_execution_version ON public.management_changelog
      (task_id, ((metadata->'record')->>'recommendation_version_id')) WHERE source='hitl_executor_v1'
      AND COALESCE(metadata->>'operation_type','apply')='apply' AND metadata->>'state'!='cancelled'""")
    op.execute("""CREATE UNIQUE INDEX uq_hitl_rollback_original ON public.management_changelog
      (((metadata->'record')->>'original_operation_id')) WHERE source='hitl_executor_v1'
      AND metadata->>'operation_type'='rollback' AND metadata->>'state'!='cancelled'""")
    op.execute(JOURNAL_GUARD)
    op.execute("""CREATE TRIGGER hitl_execution_verification_guard BEFORE INSERT OR UPDATE ON public.management_changelog
      FOR EACH ROW EXECUTE FUNCTION public.protect_hitl_verification()""")


def downgrade() -> None:
    op.execute("LOCK TABLE public.management_changelog IN ACCESS EXCLUSIVE MODE")
    if op.get_bind().scalar(text("""SELECT EXISTS(SELECT 1 FROM public.management_changelog WHERE source='hitl_executor_v1'
      AND (metadata ? 'verification' OR metadata ? 'public_baseline' OR metadata ? 'rollback_operation_id' OR metadata->>'operation_type'='rollback'))""")):
        raise RuntimeError("HITL_VERIFICATION_DOWNGRADE_WOULD_LOSE_HISTORY")
    op.execute("DROP TRIGGER hitl_execution_verification_guard ON public.management_changelog")
    op.execute("DROP FUNCTION public.protect_hitl_verification()")
    op.execute("DROP INDEX public.uq_hitl_rollback_original")
    op.execute("DROP INDEX public.uq_hitl_execution_version")
    op.execute("""CREATE UNIQUE INDEX uq_hitl_execution_version ON public.management_changelog
      (task_id, ((metadata->'record')->>'recommendation_version_id')) WHERE source='hitl_executor_v1' AND metadata->>'state'!='cancelled'""")
    op.execute("ALTER TABLE public.management_changelog DROP CONSTRAINT ck_hitl_execution_record")
    op.execute(PREVIOUS_CHECK)
    op.execute(PREVIOUS_GUARD)

RECORD_CHECK = """ALTER TABLE public.management_changelog ADD CONSTRAINT ck_hitl_execution_record CHECK
(source != 'hitl_executor_v1' OR ((entity_type='deployment_operation' AND entity_id=id::text
 AND task_id IS NOT NULL AND metadata->'record'->>'operation_id'=id::text
 AND metadata->'record'->>'task_id'=task_id::text AND metadata->'record'->>'project_id'=project_id::text
 AND metadata->'record'->>'recommendation_version_id' IS NOT NULL
 AND metadata->>'state' IN ('queued','preflight','dry_run','ready','rejected','unsupported','conflict','failed_before_apply',
   'applying','outcome_unknown','needs_reconciliation','applied_unverified','no_op','verifying','verified','verification_failed','rollback_applied_unverified','rollback_verifying','rollback_verified','rollback_verification_failed','rollback_in_progress','rolled_back','cancelled')
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
 "applying":["applying","outcome_unknown","needs_reconciliation","applied_unverified","no_op","rollback_applied_unverified"],
 "outcome_unknown":["outcome_unknown","needs_reconciliation","applied_unverified","no_op","rollback_applied_unverified"],
 "needs_reconciliation":["needs_reconciliation","applied_unverified","no_op","rollback_applied_unverified"],
 "applied_unverified":["applied_unverified","verifying","verification_failed","rollback_in_progress"],
 "no_op":["no_op","verifying","verification_failed","rollback_in_progress"],
 "verifying":["verifying","verified","verification_failed","rollback_in_progress"],
 "verified":["verified","rollback_in_progress"],"verification_failed":["verification_failed","rollback_in_progress"],
 "rollback_in_progress":["rollback_in_progress","rolled_back"],"rolled_back":["rolled_back"],
 "rollback_applied_unverified":["rollback_applied_unverified","rollback_verifying","rollback_verification_failed"],
 "rollback_verifying":["rollback_verifying","rollback_verified","rollback_verification_failed"],
 "rollback_verified":["rollback_verified"],"rollback_verification_failed":["rollback_verification_failed"],"cancelled":["cancelled"]}'::jsonb;
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
    OR NEW.metadata->'operation_type' IS DISTINCT FROM OLD.metadata->'operation_type'
    OR NEW.metadata->'intent' IS DISTINCT FROM OLD.metadata->'intent'
    OR NEW.metadata->'intent_hash' IS DISTINCT FROM OLD.metadata->'intent_hash'
    OR (NOT freezing AND (NEW.before_value IS DISTINCT FROM OLD.before_value OR NEW.after_value IS DISTINCT FROM OLD.after_value
       OR NEW.metadata->'record' IS DISTINCT FROM OLD.metadata->'record'
       OR NEW.metadata->'record_hash' IS DISTINCT FROM OLD.metadata->'record_hash'
       OR NEW.metadata->'prepared' IS DISTINCT FROM OLD.metadata->'prepared')) THEN
    RAISE EXCEPTION 'HITL_EXECUTION_IMMUTABLE';
  END IF;
  IF (transitions->(OLD.metadata->>'state') ? (NEW.metadata->>'state')) IS NOT TRUE
    OR (OLD.metadata->>'state' IN ('cancelled','needs_reconciliation','rejected','unsupported','conflict','failed_before_apply')
      AND NEW.metadata->>'state'=OLD.metadata->>'state' AND
      (NEW.metadata IS DISTINCT FROM OLD.metadata OR NEW.applied IS DISTINCT FROM OLD.applied)) THEN
    RAISE EXCEPTION 'HITL_EXECUTION_INVALID_TRANSITION';
  END IF;
 ELSIF TG_OP='UPDATE' AND NEW.source='hitl_executor_v1' THEN RAISE EXCEPTION 'HITL_EXECUTION_IMMUTABLE';
 END IF;
 IF TG_OP='DELETE' THEN RETURN OLD; END IF;
 RETURN NEW;
END; $$"""

PREVIOUS_CHECK = """ALTER TABLE public.management_changelog ADD CONSTRAINT ck_hitl_execution_record CHECK
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

PREVIOUS_GUARD = """CREATE OR REPLACE FUNCTION public.protect_hitl_execution_record() RETURNS trigger LANGUAGE plpgsql AS $$
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
