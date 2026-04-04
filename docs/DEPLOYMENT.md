# Deployment

## Local Stack

Main local stack file:

- `docker-compose.yml`

Core services:

- `postgres`
- `redis`
- `rabbitmq`
- `api-gateway`
- `audit-service`
- `semantic-service`
- `reporting-service`
- `management-service`
- `client-api-gateway`
- `tilda-adapter`

## Basic Run

```powershell
docker compose up -d --build
```

Stop stack:

```powershell
docker compose down
```

Full reset with volumes:

```powershell
docker compose down -v
```

## Environment

Important variables:

- `DATABASE_URL` or service-specific database URLs
- `REDIS_URL`
- `RABBITMQ_USER`
- `RABBITMQ_PASSWORD`
- `JWT_SECRET_KEY`
- `INTERNAL_API_KEY`
- `WORDPRESS_BASE_URL`
- `WORDPRESS_HMAC_SECRET`
- `CLIENT_API_HMAC_SECRET`
- `TILDA_PUBLIC_KEY`
- `TILDA_SECRET_KEY`
- `TILDA_INTERNAL_API_KEY`
- `TILDA_WEBHOOK_SECRET`

## Production Notes

- Disable public docs endpoints where already controlled by environment.
- Replace default secrets from compose files.
- Keep RabbitMQ, PostgreSQL, and Redis on private networks.
- Place `api-gateway` and frontend behind reverse proxy with TLS.
- Keep adapter credentials in secret manager or deployment vault, not in repository.

## WordPress Deployment

1. Build plugin ZIP with:

```powershell
powershell -ExecutionPolicy Bypass -File adapters/wordpress-plugin/build-package.ps1 -Version 0.2.0
```

2. Install plugin in WordPress.
3. Configure:
   - `Project ID`
   - `HMAC Secret`
   - `Max timestamp drift`
4. Verify:
   - `/wp-json/seo-master/v1/health`
   - signed `PATCH` routes for `meta`, `schema`, `interlinks`

## Tilda Deployment

Required env:

- `TILDA_PUBLIC_KEY`
- `TILDA_SECRET_KEY`
- `TILDA_INTERNAL_API_KEY`

Optional for local-only mock checks:

- `TILDA_MOCK_MODE=true`

Health endpoint:

- `GET /health`

Internal apply routes:

- `POST /internal/tilda/meta`
- `POST /internal/tilda/schema`
- `POST /internal/tilda/interlinks`

## E2E Stand

See:

- `tests/e2e/README.md`
- `tests/e2e/docker-compose.e2e.yml`
- `tests/e2e/run_extended_e2e.ps1`

Manual run:

```powershell
docker compose -f docker-compose.dev.yml -f tests/e2e/docker-compose.e2e.yml up -d --build
python -m pytest tests/e2e/test_extended_stack.py -q
docker compose -f docker-compose.dev.yml -f tests/e2e/docker-compose.e2e.yml down -v
```
