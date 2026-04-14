from celery import Celery
from celery.schedules import crontab

from config.logging_config import get_logger
from services.management_service.config import settings

logger = get_logger(__name__)

MANAGEMENT_TASK_MODULES = (
    "services.management_service.tasks.periodic_tasks",
    "services.management_service.tasks.orchestration_tasks",
)

celery_app = Celery(
    "management_service",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=list(MANAGEMENT_TASK_MODULES),
)


def _cron_from_expr(expr: str):
    try:
        minute, hour, day_of_month, month_of_year, day_of_week = expr.split()
    except ValueError:
        logger.warning(
            "Invalid DEFAULT_FFSCORE_SCHEDULE, using 0 3 * * *",
            extra={"value": expr},
        )
        return crontab(minute=0, hour=3)

    return crontab(
        minute=minute,
        hour=hour,
        day_of_month=day_of_month,
        month_of_year=month_of_year,
        day_of_week=day_of_week,
    )


celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_always_eager=settings.CELERY_TASK_ALWAYS_EAGER,
    broker_connection_retry_on_startup=True,
    task_default_queue="management",
    task_routes={
        "services.management_service.tasks.periodic_tasks.*": {
            "queue": "management_periodic",
            "routing_key": "management.periodic",
        },
        "services.management_service.tasks.orchestration_tasks.*": {
            "queue": "management",
            "routing_key": "management.orchestration",
        },
    },
    imports=MANAGEMENT_TASK_MODULES,
)

celery_app.conf.beat_schedule = {
    "daily_ff_score_recalculation": {
        "task": "services.management_service.tasks.periodic_tasks.daily_ff_score_recalculation",
        "schedule": _cron_from_expr(settings.DEFAULT_FFSCORE_SCHEDULE),
        "options": {"queue": "management_periodic", "routing_key": "management.periodic"},
    }
}

celery_app.autodiscover_tasks(["services.management_service.tasks"], force=True)
