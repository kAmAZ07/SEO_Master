# API

## Public Audit

Base public entrypoint: `api-gateway`

- `POST /api/public/quick-audit`
  Starts a public audit for a URL and returns an audit identifier.
- `GET /api/public/audit-status/{uid}`
  Returns current status, progress, summary, findings, and crawled pages.
- `GET /api/public/rate-limit-info`
  Returns current public audit rate-limit state for caller IP.

Downstream audit-service endpoints:

- `POST /audit/public`
- `GET /audit/{audit_id}`
- `GET /health`

## Auth And User Area

Base protected entrypoint: `api-gateway`

- `POST /api/auth/register`
- `POST /api/auth/login`
- `POST /api/auth/refresh`
- `GET /api/auth/me`
- `PATCH /api/auth/profile`
- `POST /api/auth/change-password`
- `POST /api/auth/logout`

## Dashboard And Projects

- `GET /api/dashboard/stats`
- `GET /api/dashboard`
- `GET /api/projects`
- `POST /api/projects`
- `GET /api/projects/{project_id}`
- `DELETE /api/projects/{project_id}`
- `GET /api/audit/history`

## HITL

Protected gateway proxies Management Service:

- `GET /api/hitl/tasks`
- `GET /api/hitl/tasks/{task_id}`
- `POST /api/hitl/tasks/{task_id}/approve`
- `POST /api/hitl/tasks/{task_id}/reject`

Native management-service routes:

- `GET /api/v1/hitl/tasks`
- `GET /api/v1/hitl/tasks/{task_id}`
- `POST /api/v1/hitl/tasks/{task_id}/approve`
- `POST /api/v1/hitl/tasks/{task_id}/reject`

## Content, Keywords, Backlinks

- `GET /api/backlinks`
- `GET /api/projects/{project_id}/backlinks`
- `POST /api/backlinks/analyze`
- `GET /api/content/optimized`
- `GET /api/projects/{project_id}/content/optimized`
- `POST /api/content/analyze`
- `POST /api/keywords/search`
- `GET /api/keywords/tracked`
- `POST /api/keywords/tracked`
- `DELETE /api/keywords/tracked/{keyword_id}`

## Semantic Service

- `POST /semantic/eeat`
- `POST /semantic/ff-score`
- `POST /semantic/drafts`
- `GET /semantic/latest/{project_id}`
- `GET /health`

## Reporting Service

- `POST /reporting/reports`
- `GET /reporting/reports/{report_id}`
- `POST /reporting/metrics`
- `GET /reporting/export/csv/{report_id}`
- `GET /health`

## Client API Gateway

Internal deploy and key-management endpoints:

- `POST /internal/deploy`
- `POST /internal/deploy/{deployment_id}/rollback`
- `GET /changes/pending/{project_id}`
- `GET /internal/keys/{project_id}`
- `POST /internal/keys/{project_id}/ensure`
- `POST /internal/keys/{project_id}/rotate`

Signed client patch endpoints:

- `PATCH /api/client/meta`
- `PATCH /api/client/schema`
- `PATCH /api/client/interlinks`

## Deployment Contract

Canonical change contract inside the platform:

```json
{
  "project_id": "project-123",
  "task_id": "task-123",
  "entity_id": "https://example.com/page",
  "entity_type": "wordpress_post",
  "change_type": "meta",
  "changes": {
    "before": {
      "title": "Old title",
      "description": "Old description"
    },
    "after": {
      "title": "New title",
      "description": "New description"
    }
  },
  "metadata": {
    "platform": "wordpress",
    "correlation_id": "corr-123"
  }
}
```

Client API Gateway converts this contract to JSON Patch before sending changes to WordPress or Tilda adapters.

Accepted patch shape for external adapter/application layer:

```json
{
  "project_id": "project-123",
  "task_id": "task-123",
  "entity_id": "https://example.com/page",
  "entity_type": "wordpress_post",
  "changes": [
    { "op": "replace", "path": "/title", "value": "New title" },
    { "op": "replace", "path": "/description", "value": "New description" }
  ],
  "metadata": {
    "platform": "wordpress"
  }
}
```

## Health Endpoints

- `api-gateway: GET /health`
- `management-service: GET /health`, `GET /health/ready`
- `audit-service: GET /health`
- `semantic-service: GET /health`
- `reporting-service: GET /health`
- `client-api-gateway: GET /health`
- `tilda-adapter: GET /health`
