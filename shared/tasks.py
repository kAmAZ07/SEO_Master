from __future__ import annotations

from typing import Any

from sqlalchemy import text

from config.celery_config import app, redis_client
from config.database_config import engine


def _run_scalar(sql: str, **params: Any) -> int:
    with engine.begin() as connection:
        value = connection.execute(text(sql), params).scalar()
    return int(value or 0)


@app.task(name="shared.tasks.cleanup_old_crawl_data")
def cleanup_old_crawl_data(retention_days: int = 30) -> dict[str, Any]:
    purged_count = _run_scalar(
        "SELECT audit_schema.cleanup_old_crawl_data(:retention_days)",
        retention_days=retention_days,
    )
    return {
        "status": "completed",
        "retention_days": retention_days,
        "purged_raw_rows": purged_count,
    }


@app.task(name="shared.tasks.cleanup_old_crawl_aggregates")
def cleanup_old_crawl_aggregates(retention_days: int = 365) -> dict[str, Any]:
    deleted_count = _run_scalar(
        "SELECT audit_schema.cleanup_old_crawl_aggregates(:retention_days)",
        retention_days=retention_days,
    )
    return {
        "status": "completed",
        "retention_days": retention_days,
        "deleted_aggregate_rows": deleted_count,
    }


@app.task(name="shared.tasks.cleanup_public_audit_results")
def cleanup_public_audit_results(retention_days: int = 7) -> dict[str, Any]:
    deleted_count = _run_scalar(
        "SELECT audit_schema.cleanup_public_audits(:retention_days)",
        retention_days=retention_days,
    )
    return {
        "status": "completed",
        "retention_days": retention_days,
        "deleted_public_audits": deleted_count,
    }


@app.task(name="shared.tasks.cleanup_expired_llm_cache")
def cleanup_expired_llm_cache() -> dict[str, Any]:
    # Redis removes TTL-bound LLM cache keys automatically; this task keeps the beat contract observable.
    return {"status": "completed", "redis_db_size": redis_client.dbsize()}


@app.task(name="shared.tasks.worker_health_check")
def worker_health_check() -> dict[str, str]:
    return {"status": "ok", "service": "celery-worker"}
