from datetime import datetime, timezone
import asyncio
from typing import Any, Dict

import httpx

from services.management_service.config import settings
from services.management_service.db.session import SessionLocal
from services.management_service.canonical_projects import active_project_targets
from config.logging_config import get_logger

from services.management_service.tasks.celery_app import celery_app

logger = get_logger(__name__)


@celery_app.task(name="services.management_service.tasks.periodic_tasks.dispatch_hitl_outbox",
                 soft_time_limit=40, time_limit=45)
def dispatch_hitl_outbox() -> Dict[str, int]:
    from services.management_service.outbox import drain_outbox
    from services.management_service.outbox_transport import publish_stored_event

    return asyncio.run(drain_outbox(SessionLocal, publish_stored_event))


@celery_app.task(
    name="services.management_service.tasks.periodic_tasks.daily_ff_score_recalculation"
)
def daily_ff_score_recalculation() -> Dict[str, Any]:
    ffscore_endpoint = "/semantic/ff-score"
    if not settings.SEMANTIC_SERVICE_URL:
        logger.warning("SEMANTIC_SERVICE_URL is not configured")
        return {"status": "skipped", "reason": "semantic_service_url_missing"}

    db = SessionLocal()
    try:
        projects = active_project_targets(db)
        total = len(projects)
        succeeded = 0
        failed = 0
        errors = []

        for project in projects:
            payload: Dict[str, Any] = {
                "project_id": str(project.id),
                "root_url": project.url,
                "content_text": "",  # Missing content is not fabricated from archived project metadata.
            }

            correlation_id = f"ffscore-recalc-{project.id}"
            try:
                with httpx.Client(timeout=settings.SERVICE_REQUEST_TIMEOUT) as client:
                    response = client.post(
                        f"{settings.SEMANTIC_SERVICE_URL}{ffscore_endpoint}",
                        json=payload,
                        headers={
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


