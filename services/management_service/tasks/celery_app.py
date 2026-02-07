from celery import Celery
from celery.schedules import crontab

from services.management_service.config import settings
from config.logging_config import get_logger

logger = get_logger(__name__)

celery_app = Celery(
    "management_service",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
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
)

celery_app.conf.beat_schedule = {
    "daily_ff_score_recalculation": {
        "task": "services.management_service.tasks.periodic_tasks.daily_ff_score_recalculation",
        "schedule": _cron_from_expr(settings.DEFAULT_FFSCORE_SCHEDULE),
    }
}

celery_app.autodiscover_tasks(["services.management_service.tasks"])
