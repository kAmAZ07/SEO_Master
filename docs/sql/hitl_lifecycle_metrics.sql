-- HITL lifecycle metrics for PostgreSQL.
--
-- Assumptions:
-- - management_service.hitl_approvals and client_api_gateway.deployment_log
--   are available in the same database or exported into one analytical schema.
-- - deployment_log.task_id stores the related HITL/task id as text.
--
-- Replace table names with schema-qualified names if your deployment uses
-- separate schemas, for example management.hitl_approvals.

WITH hitl AS (
    SELECT
        id,
        task_id::text AS task_id,
        project_id::text AS project_id,
        status::text AS status,
        created_at AS hitl_created_at,
        approved_at,
        rejected_at
    FROM hitl_approvals
),
deployments AS (
    SELECT
        id AS deployment_id,
        task_id::text AS task_id,
        project_id::text AS project_id,
        status::text AS deployment_status,
        created_at AS deployment_created_at,
        applied_at
    FROM deployment_log
),
lifecycle AS (
    SELECT
        h.project_id,
        h.task_id,
        h.status AS hitl_status,
        d.deployment_status,
        h.hitl_created_at,
        h.approved_at,
        h.rejected_at,
        d.deployment_created_at,
        d.applied_at,
        EXTRACT(EPOCH FROM (h.approved_at - h.hitl_created_at)) AS approval_seconds,
        EXTRACT(EPOCH FROM (d.deployment_created_at - h.approved_at)) AS approval_to_deploy_received_seconds,
        EXTRACT(EPOCH FROM (d.applied_at - d.deployment_created_at)) AS deploy_apply_seconds,
        EXTRACT(EPOCH FROM (d.applied_at - h.hitl_created_at)) AS total_hitl_to_applied_seconds
    FROM hitl h
    LEFT JOIN deployments d
        ON d.task_id = h.task_id
        AND d.project_id = h.project_id
)
SELECT
    project_id,
    COUNT(*) AS hitl_total,
    COUNT(*) FILTER (WHERE UPPER(hitl_status) = 'PENDING') AS pending_total,
    COUNT(*) FILTER (WHERE approved_at IS NOT NULL) AS approved_total,
    COUNT(*) FILTER (WHERE rejected_at IS NOT NULL) AS rejected_total,
    COUNT(*) FILTER (WHERE applied_at IS NOT NULL) AS deployed_total,
    ROUND(AVG(approval_seconds)::numeric, 2) AS avg_approval_seconds,
    ROUND(percentile_cont(0.50) WITHIN GROUP (ORDER BY approval_seconds)::numeric, 2) AS p50_approval_seconds,
    ROUND(percentile_cont(0.95) WITHIN GROUP (ORDER BY approval_seconds)::numeric, 2) AS p95_approval_seconds,
    ROUND(percentile_cont(0.99) WITHIN GROUP (ORDER BY approval_seconds)::numeric, 2) AS p99_approval_seconds,
    ROUND(AVG(deploy_apply_seconds)::numeric, 2) AS avg_deploy_apply_seconds,
    ROUND(percentile_cont(0.95) WITHIN GROUP (ORDER BY deploy_apply_seconds)::numeric, 2) AS p95_deploy_apply_seconds,
    ROUND(percentile_cont(0.99) WITHIN GROUP (ORDER BY deploy_apply_seconds)::numeric, 2) AS p99_deploy_apply_seconds,
    ROUND(AVG(total_hitl_to_applied_seconds)::numeric, 2) AS avg_total_hitl_to_applied_seconds,
    ROUND(percentile_cont(0.95) WITHIN GROUP (ORDER BY total_hitl_to_applied_seconds)::numeric, 2) AS p95_total_hitl_to_applied_seconds,
    ROUND(percentile_cont(0.99) WITHIN GROUP (ORDER BY total_hitl_to_applied_seconds)::numeric, 2) AS p99_total_hitl_to_applied_seconds
FROM lifecycle
GROUP BY project_id
ORDER BY project_id;

-- Per-task details for validation and screenshots.
SELECT
    h.project_id::text AS project_id,
    h.task_id::text AS task_id,
    h.status::text AS hitl_status,
    h.created_at AS hitl_created_at,
    h.approved_at,
    d.created_at AS deployment_created_at,
    d.applied_at,
    d.status::text AS deployment_status,
    ROUND(EXTRACT(EPOCH FROM (h.approved_at - h.created_at))::numeric, 2) AS approval_seconds,
    ROUND(EXTRACT(EPOCH FROM (d.applied_at - d.created_at))::numeric, 2) AS deploy_apply_seconds,
    ROUND(EXTRACT(EPOCH FROM (d.applied_at - h.created_at))::numeric, 2) AS total_hitl_to_applied_seconds
FROM hitl_approvals h
LEFT JOIN deployment_log d
    ON d.task_id::text = h.task_id::text
    AND d.project_id::text = h.project_id::text
ORDER BY h.created_at DESC
LIMIT 100;
