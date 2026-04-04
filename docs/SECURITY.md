# Security

## Trust Boundaries

- Public callers interact only with `api-gateway`.
- Internal service deploy commands go through `client-api-gateway`.
- WordPress accepts only signed HMAC requests.
- Tilda internal apply routes accept only internal API key.

## Authentication

### User Auth

- JWT access and refresh tokens are issued by `api-gateway`.
- Passwords are stored as bcrypt hashes.

### Internal Service Auth

- `X-Internal-API-Key` protects internal deploy and internal key-rotation routes.

### CMS Deploy Auth

- WordPress uses HMAC headers:
  - `X-Project-ID`
  - `X-Timestamp`
  - `X-Signature`
  - optional `X-Key-ID`
- Signature source:
  - `timestamp + METHOD + path + sha256(body)`

## Key Management

`client-api-gateway` supports per-project active keys and rotation windows:

- `POST /internal/keys/{project_id}/ensure`
- `POST /internal/keys/{project_id}/rotate`
- `GET /internal/keys/{project_id}`

## Replay Protection

- Timestamp drift is validated on WordPress side.
- HMAC body hash prevents payload tampering.
- Old signatures should be rejected after drift window expires.

## Public Audit Protections

- Request rate-limiting via Redis.
- Localhost and private-network URL blocking.
- Pre-checks against unsafe targets.

## Secrets Handling

- Do not commit production secrets.
- Use `.env` only for local development.
- Prefer environment injection from CI/CD or secret manager in shared environments.

## Logging

- Correlation IDs are propagated between services.
- Secrets must not be logged.
- Deployment logs may store payload metadata, but never raw HMAC secrets.

## Rollback And Auditability

- Every deploy is written to `deployment_log`.
- Rollback uses stored reverse operations or diff metadata.
- Failed deployments remain auditable through deployment status and error fields.
