# Architecture

## Overview

SEO Master is a microservice-based SEO automation platform with a public audit entrypoint, protected user cabinet, orchestration layer, semantic scoring, reporting, and deploy adapters for external CMS platforms.

## Main Components

- `api-gateway`
  Public and authenticated frontend-facing API. Handles auth, public audit entrypoints, project views, and proxies HITL actions.
- `audit-service`
  Runs public crawl pipeline, robots checks, meta analysis, JSON-LD validation, broken-link analysis, and CWV integration.
- `semantic-service`
  Calculates E-E-A-T and FF-Score, generates content drafts, stores latest semantic state.
- `reporting-service`
  Builds reports, metrics, and CSV exports.
- `management-service`
  Orchestrates optimization lifecycle, creates tasks, handles HITL, consumes domain events, and triggers deploys.
- `client-api-gateway`
  Secure deployment gateway. Verifies HMAC for client patch endpoints, stores deployment logs, dispatches to WordPress or Tilda.
- `wordpress-plugin`
  Applies signed JSON Patch-like changes inside WordPress.
- `tilda-adapter`
  Applies internal platform changes to Tilda pages.
- `frontend`
  React SPA for dashboard, projects, audit, content, backlinks, keyword research, and settings.

## Data Flow

### Public Audit

1. Frontend or external caller sends URL to `api-gateway`.
2. `api-gateway` validates and rate-limits request.
3. `audit-service` stores queued audit and starts background pipeline.
4. Crawl results are persisted and published as domain event.
5. Frontend polls status through `api-gateway`.

### Optimization Cycle

1. `management-service` starts saga for project URL.
2. Audit result is requested from `audit-service`.
3. `semantic-service` calculates FF-Score and E-E-A-T.
4. Draft generation is requested from `semantic-service`.
5. If HITL is required, approval record is created and exposed to UI.
6. After approval, `client-api-gateway` dispatches changes to WordPress or Tilda.
7. If deployment fails after a previous apply, compensation triggers rollback.

## Event Bus

RabbitMQ topic exchange: `seo_master.events`

Main routing keys:

- `audit.crawl.completed`
- `semantic.ffscore.recalculated`
- `management.hitl.approval_required`
- `management.optimization.completed`
- `management.optimization.failed`

## Persistence

- PostgreSQL
  Primary relational storage for users, projects, audits, scores, reports, tasks, HITL, and deployment logs.
- Redis
  Rate limiting and caching.
- RabbitMQ
  Durable asynchronous event delivery.

## Reliability Notes

- Service-to-service HTTP requests use retry logic in critical deploy/orchestration paths.
- Deployments are logged before and after dispatch.
- Rollback uses stored diff or reverse patch operations when a deployment must be compensated.
- Background consumers are isolated from request-response path.

## Boundaries

- `management-service` owns orchestration state.
- `client-api-gateway` owns deployment security, patch normalization, and downstream adapter dispatch.
- CMS adapters only apply changes and do not decide business workflow.
