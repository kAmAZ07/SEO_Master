from datetime import datetime, timezone
from typing import Any, Dict

import httpx

from services.management_service.config import settings
from services.management_service.db.session import SessionLocal
from services.management_service.db.models import Project
from config.logging_config import get_logger

from services.management_service.tasks.celery_app import celery_app

logger = get_logger(__name__)


@celery_app.task(
    name="services.management_service.tasks.periodic_tasks.daily_ff_score_recalculation"
)
def daily_ff_score_recalculation() -> Dict[str, Any]:
    ffscore_endpoint = "/internal/ff-score/calculate"
    if not settings.SEMANTIC_SERVICE_URL:
        logger.warning("SEMANTIC_SERVICE_URL is not configured")
        return {"status": "skipped", "reason": "semantic_service_url_missing"}

    if not settings.INTERNAL_API_KEY:
        logger.warning("INTERNAL_API_KEY is not configured")
        return {"status": "skipped", "reason": "internal_api_key_missing"}

    db = SessionLocal()
    try:
        projects = db.query(Project).filter(Project.is_active.is_(True)).all()
        total = len(projects)
        succeeded = 0
        failed = 0
        errors = []

        for project in projects:
            metadata = project.metadata or {}
            payload: Dict[str, Any] = {"project_id": str(project.id)}
            latest_crawl_id = metadata.get("latest_crawl_id") or metadata.get("crawl_id")
            if latest_crawl_id:
                payload["crawl_id"] = latest_crawl_id

            url_value = metadata.get("root_url") or metadata.get("url")
            if not url_value:
                if isinstance(project.domain, str) and "://" in project.domain:
                    url_value = project.domain
            if url_value:
                payload["url"] = url_value

            correlation_id = f"ffscore-recalc-{project.id}"
            try:
                with httpx.Client(timeout=settings.SERVICE_REQUEST_TIMEOUT) as client:
                    response = client.post(
                        f"{settings.SEMANTIC_SERVICE_URL}{ffscore_endpoint}",
                        json=payload,
                        headers={
                            "X-Internal-API-Key": settings.INTERNAL_API_KEY,
                            "X-Correlation-ID": correlation_id,
                        },
                    )
                    response.raise_for_status()
                succeeded += 1
            except Exception as exc:
                failed += 1
                errors.append({"project_id": str(project.id), "error": str(exc)})
                logger.error(
                    "FFScore recalculation failed",
                    extra={"project_id": str(project.id), "error": str(exc)},
                )

        return {
            "status": "completed",
            "total_projects": total,
            "succeeded": succeeded,
            "failed": failed,
            "errors": errors,
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
    finally:
        db.close()
