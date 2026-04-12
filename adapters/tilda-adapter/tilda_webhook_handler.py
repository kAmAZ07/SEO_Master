from typing import Any, Dict

import httpx
from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel, Field

from config import settings

router = APIRouter(prefix='/webhook', tags=['webhook'])


class TildaWebhookEvent(BaseModel):
    project_id: str = Field(..., min_length=1)
    page_id: str = Field(..., min_length=1)
    event: str = Field(default='publish')
    payload: Dict[str, Any] = Field(default_factory=dict)


@router.post('/published')
async def on_page_published(
    event: TildaWebhookEvent,
    x_tilda_signature: str | None = Header(default=None, alias='X-Tilda-Signature'),
):
    if settings.webhook_secret:
        if not x_tilda_signature or x_tilda_signature != settings.webhook_secret:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Invalid webhook signature')

    forward_result: Dict[str, Any] | None = None
    if settings.webhook_forward_url:
        headers = {'Content-Type': 'application/json'}
        if settings.webhook_forward_api_key:
            headers['X-Internal-API-Key'] = settings.webhook_forward_api_key

        async with httpx.AsyncClient(timeout=settings.request_timeout_seconds) as client:
            try:
                response = await client.post(
                    settings.webhook_forward_url,
                    json={
                        'project_id': event.project_id,
                        'page_id': event.page_id,
                        'page_url': event.payload.get('url') if isinstance(event.payload, dict) else None,
                        'event': event.event,
                        'payload': event.payload,
                    },
                    headers=headers,
                )
                response.raise_for_status()
                forward_result = response.json()
            except httpx.HTTPError as exc:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f'Failed to forward Tilda webhook: {exc}',
                ) from exc

    return {
        'status': 'queued' if forward_result else 'accepted',
        'project_id': event.project_id,
        'page_id': event.page_id,
        'event': event.event,
        'forward_result': forward_result,
    }
