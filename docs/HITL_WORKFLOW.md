# HITL Workflow

## Purpose

Human-in-the-Loop is used for SEO changes that should be reviewed before publishing to a client website.

## Lifecycle

1. `management-service` creates optimization task.
2. Saga requests crawl and semantic calculations.
3. Drafted changes are transformed into diff:
   - `before`
   - `after`
4. `HITLApproval` record is created with impact score and recommendation.
5. UI loads pending approvals through `api-gateway`.
6. Reviewer approves or rejects.
7. If approved and `auto_deploy=true`, `management-service` sends deploy command to `client-api-gateway`.
8. Deployment status is stored and completion event is published.

## Main States

- `PENDING`
- `APPROVED`
- `REJECTED`

Saga-adjacent states:

- `AWAITING_HITL`
- `HITL_APPROVED`
- `HITL_REJECTED`

## Approval API

- `GET /api/v1/hitl/tasks`
- `GET /api/v1/hitl/tasks/{task_id}`
- `POST /api/v1/hitl/tasks/{task_id}/approve`
- `POST /api/v1/hitl/tasks/{task_id}/reject`

## Approval Payload

Approval request:

```json
{
  "user_id": "reviewer-1",
  "comment": "Looks correct",
  "auto_deploy": true
}
```

Rejection request:

```json
{
  "user_id": "reviewer-1",
  "comment": "Needs rewrite",
  "rejection_reason": "Brand voice mismatch"
}
```

## Deployment After Approval

When auto-deploy is enabled:

1. `management-service` converts `before/after` diff to canonical deploy payload.
2. `client-api-gateway` normalizes it to patch operations.
3. Target adapter applies changes.
4. If post-apply failure happens, compensation triggers rollback.

## Operational Recommendations

- Require HITL for title, description, H1, schema, and interlink changes on production projects.
- Allow automatic deploy only for low-risk projects or trusted reviewers.
- Keep reviewer identity in task metadata for audit trail.
