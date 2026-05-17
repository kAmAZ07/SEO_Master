# Quality Report

Generated locally on 2026-05-18.

## Current Verified Results

Backend unit tests:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\unit -q
```

Result:

```text
40 passed, 70 warnings
```

Backend coverage:

```powershell
.\.venv\Scripts\python.exe -m pytest tests\unit --cov=services --cov=config --cov=database --cov=adapters --cov-report=term-missing --cov-report=xml --cov-report=html
```

Result:

```text
TOTAL: 7040 statements, 4091 missed, 42% coverage
```

Generated artifacts:

- `coverage.xml`
- `htmlcov/`

Frontend build:

```powershell
npm run build
```

Result:

```text
TypeScript and Vite production build completed successfully.
```

## Frontend Test Coverage

Frontend unit/component coverage is now available through Vitest and React Testing Library.

```powershell
cd frontend
npm run test:coverage
```

Result:

```text
3 test files passed, 6 tests passed
Statements: 7.67%
Branches: 6.96%
Functions: 7.67%
Lines: 7.78%
```

Generated artifacts:

- `frontend/coverage/`

Currently covered:

- HITL pending diff rendering and approve flow
- HITL empty state
- HITL API normalization and approve/reject calls
- shared `Input` component, including password visibility toggle
- WordPress HMAC secret metadata is covered by backend unit tests

Recommended next step:

- Add component coverage for auth, public audit form, dashboard widgets, and project integration forms.
- Add store-level tests for dashboard/HITL async thunks.

## Performance Metrics

No measured RPS, p95, or p99 numbers are available yet in this local workspace.

Existing instrumentation:

- `hitl_processing_duration_seconds`
- `deployment_duration_seconds`
- deployment request and error counters

Gaps:

- Docker daemon is not running in this environment.
- `k6` is not installed locally.
- Local `http://localhost:8005/health` did not respond.
- No saved Prometheus report exists yet.

Recommended next step:

- Run `tests/load/client_api_gateway_k6.js` against staging or a running local stack.
- Record RPS, p95 latency, p99 latency, error rate, and rate-limit behavior.

## HITL Lifecycle Metrics

The schema has enough timestamps to measure the lifecycle:

- HITL created: `HITLApproval.created_at`
- HITL approved: `HITLApproval.approved_at`
- Deploy received: `DeploymentLog.created_at`
- Deploy applied: `DeploymentLog.applied_at`

Not yet measured:

- generation to approval time
- approval to deploy time
- generation to applied time

Measurement artifact:

- `docs/sql/hitl_lifecycle_metrics.sql`

Run this query against a database or analytical export containing `hitl_approvals` and `deployment_log`.

## HMAC Status

The HMAC contract is implemented and documented in `docs/HMAC_CONTRACT.md`.

Signature formula:

```text
HMAC_SHA256(secret, timestamp + METHOD_UPPER + path_with_query + sha256(raw_body))
```

WordPress integration now supports server-side HMAC secret generation:

- `POST /api/projects/{project_id}/integrations/wordpress/secret` generates a cryptographically strong secret.
- The generated secret is returned to the UI once.
- The secret is stored only inside encrypted project integration credentials.
- The UI later shows `key_id`, fingerprint, expiration, and grace-period metadata.
- `POST /api/projects/{project_id}/integrations/wordpress/rotate` generates a replacement secret and records old-key grace metadata.
- `POST /api/projects/{project_id}/integrations/wordpress/verify` checks the WordPress plugin health endpoint after the secret is copied into `wp-config.php`.

## FF-Score Status

FF-Score is implemented in `services/semantic_service/scoring/ff_score_calculator.py`.

Formula:

```text
FF = 0.30 * freshness + 0.40 * familiarity + 0.30 * quality
```

Current thresholds:

```json
{"rescue_lt": 40.0, "growth_gt": 60.0}
```

## Rollback Status

Current state:

- Rollback is coordinated by `client-api-gateway`.
- Reverse patch operations are built from stored deployment changes.
- Management compensation calls `/internal/deploy/{deployment_id}/rollback`.
- WordPress supports applying `add`, `replace`, and `remove` operations.
- Tilda adapter apply routes are mostly oriented around `add`/`replace`; rollback support is partial.
- Unit tests now verify reverse patch generation, target platform selection, and rollback dispatch.

Recommended next step:

- Add e2e test: apply change, verify changed state, rollback, verify old state.
- Add explicit Tilda reverse/remove handling for meta/schema/interlinks.

## Main Quality Risks

- Backend coverage is currently low at 42%.
- Frontend automated coverage exists, but is still low at 7.78% lines.
- Performance claims cannot be made until load tests are run.
- Several Pydantic V1-style validators produce deprecation warnings under Pydantic V2.
- Some Russian UI/documentation strings still show encoding damage in source files.

## Suggested Quality Targets

- Backend unit coverage: at least 60% short term, 70%+ after integration-heavy modules are covered.
- Frontend coverage: at least smoke/component coverage for user-critical flows.
- Client API Gateway load test: publish p95/p99 latency and error rate.
- HITL lifecycle: publish average and p95 time from approval to applied deployment.
- Rollback: prove with e2e tests for WordPress and Tilda.

## Screenshots

No screenshots were generated in this workspace.

Attempted local capture was blocked because the sandbox denied browser subprocess launch (`WinError 5`). To produce evidence, run the built frontend against a running API or mocked staging data and capture:

- public audit form
- dashboard
- HITL diff/approval screen
