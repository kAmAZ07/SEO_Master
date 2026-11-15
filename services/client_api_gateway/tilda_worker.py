"""Lifecycle-owned reader; only durable hints may schedule authorized GET calls."""
import asyncio
import logging
from services.client_api_gateway.config import settings
from services.client_api_gateway.tilda_sync import TildaReader

logger = logging.getLogger(__name__)


async def run() -> None:
    reader = TildaReader()
    while True:
        try:
            await reader.once()
        except Exception:
            logger.warning('Tilda read worker unavailable; durable lease and quota retained')
        await asyncio.sleep(5)


def start() -> asyncio.Task[None] | None:
    return asyncio.create_task(run(), name='tilda-authorized-read') if configured() else None


def configured() -> bool:
    return bool(settings.TILDA_SYNC_ENABLED and settings.TILDA_ADAPTER_URL
                and len(settings.TILDA_INTERNAL_API_KEY or '') >= 32 and len(settings.INTERNAL_API_KEY or '') >= 32)
