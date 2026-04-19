from typing import Any, Dict, Optional

import httpx
from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel, Field

from config import settings
from internal_routes import build_tilda_change_set, extract_tilda_page_snapshot
from tilda_api_client import TildaAPIClient

router = APIRouter(prefix='/webhook', tags=['webhook'])


class TildaWebhookEvent(BaseModel):
    project_id: str = Field(..., min_length=1)
    page_id: str = Field(..., min_length=1)
    event: str = Field(default='publish')
    payload: Dict[str, Any] = Field(default_factory=dict)


def _payload_page_url(payload: Dict[str, Any], snapshot: Dict[str, Any]) -> Optional[str]:
    if isinstance(payload, dict):
        for key in ('url', 'page_url', 'published_url', 'link'):
            value = payload.get(key)
            if value:
                return str(value)
    value = snapshot.get('page_url')
    return str(value) if value else None


async def _fetch_current_page(page_id: str) -> Dict[str, Any]:
    client = TildaAPIClient(
        public_key=settings.public_key,
        secret_key=settings.secret_key,
        mock_mode=settings.mock_mode,
    )
    try:
        return await client.get_page(page_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail='Tilda API credentials are not configured',
        ) from exc
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f'Tilda API returned {exc.response.status_code} while loading page {page_id}',
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f'Failed to load Tilda page {page_id}: {exc}',
        ) from exc


@router.post('/published')
async def on_page_published(
    event: TildaWebhookEvent,
    x_tilda_signature: str | None = Header(default=None, alias='X-Tilda-Signature'),
):
    if settings.webhook_secret:
        if not x_tilda_signature or x_tilda_signature != settings.webhook_secret:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='Invalid webhook signature')

    if not settings.webhook_forward_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail='TILDA_WEBHOOK_FORWARD_URL is not configured',
        )
    if not settings.webhook_forward_api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail='TILDA_WEBHOOK_FORWARD_API_KEY is not configured',
        )

    page_response = await _fetch_current_page(event.page_id)
    page_snapshot = extract_tilda_page_snapshot(page_response)
    page_url = _payload_page_url(event.payload, page_snapshot)
    change_set = build_tilda_change_set(
        project_id=event.project_id,
        page_id=event.page_id,
        event=event.event,
        webhook_payload=event.payload,
        page_response=page_response,
        page_url=page_url,
    )

    headers = {'Content-Type': 'application/json'}
    headers['X-Internal-API-Key'] = settings.webhook_forward_api_key

    async with httpx.AsyncClient(timeout=settings.request_timeout_seconds) as client:
        try:
            response = await client.post(
                settings.webhook_forward_url,
                json={
                    'project_id': event.project_id,
                    'page_id': event.page_id,
                    'page_url': page_url,
                    'event': event.event,
                    'payload': event.payload,
                    'page_snapshot': page_snapshot,
                    'change_set': change_set,
                },
                headers=headers,
            )
            response.raise_for_status()
            try:
                forward_result = response.json()
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail='Tilda webhook forward target returned non-JSON response',
                ) from exc
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f'Failed to forward Tilda webhook: {exc}',
            ) from exc

    return {
        'status': forward_result.get('status') or 'queued',
        'project_id': event.project_id,
        'page_id': event.page_id,
        'event': event.event,
        'page_url': page_url,
        'change_set_id': change_set.get('change_set_id'),
        'task_id': forward_result.get('task_id'),
        'log_id': forward_result.get('log_id'),
        'forward_result': forward_result,
    }
