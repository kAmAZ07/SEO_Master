from typing import Any, Dict

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

    return {
        'status': 'accepted',
        'project_id': event.project_id,
        'page_id': event.page_id,
        'event': event.event,
    }
