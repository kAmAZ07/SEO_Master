"""Lifespan-owned status recovery; no user tokens and no CMS mutation scheduling."""
import asyncio
import logging

from services.client_api_gateway.config import settings
from services.client_api_gateway.hitl_executor import StatusReconciler
from services.client_api_gateway.public_verifier import PublicVerifier

logger = logging.getLogger(__name__)


def configured() -> bool:
    return bool(settings.EXECUTION_RECOVERY_ENABLED and len(settings.INTERNAL_API_KEY or "") >= 32
                and len(settings.MANAGEMENT_EXECUTOR_API_KEY or "") >= 32)


async def run() -> None:
    reconciler = StatusReconciler()
    verifier = PublicVerifier()
    while True:
        try:
            await reconciler.once()
        except Exception:
            # URLs, credentials, user tokens and raw provider errors never enter logs.
            logger.warning("Execution status recovery unavailable; durable lease/budget retained")
        try:
            await verifier.once()
        except Exception:
            logger.warning("Public verification unavailable; durable lease/budget retained")
        await asyncio.sleep(5)


def start() -> asyncio.Task[None] | None:
    return asyncio.create_task(run(), name="client-execution-status-recovery") if configured() else None


async def stop(task: asyncio.Task[None] | None) -> None:
    if task is not None:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass  # A pending lease remains spent and is recovered only after expiry.
