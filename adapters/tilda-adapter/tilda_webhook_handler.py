"""Official callback is GET + plain ok; only a durable downstream ACK permits ok."""
import asyncio
import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse

from config import settings
from internal_routes import require_internal_key
from shared.tilda_contract import PublishHint

router = APIRouter(tags=['webhook'])


async def accept_hint(hint: PublishHint) -> PlainTextResponse:
    if not settings.webhook_forward_url or len(settings.webhook_forward_api_key or '') < 32:
        raise HTTPException(503, 'tilda_hint_store_unconfigured')
    try:
        async with asyncio.timeout(3):
            async with httpx.AsyncClient(timeout=2., trust_env=False, follow_redirects=False) as client:
                response = await client.post(settings.webhook_forward_url, json=hint.model_dump(),
                    headers={'X-Internal-API-Key': settings.webhook_forward_api_key or ''})
    except (httpx.HTTPError, TimeoutError):
        raise HTTPException(503, 'tilda_hint_store_unavailable') from None
    if response.status_code != 200:
        code = response.status_code if response.status_code in {401, 404, 422, 429} else 503
        raise HTTPException(code, 'tilda_hint_not_accepted')
    try:
        result = response.json()
        if result['status'] not in {'queued', 'duplicate'} or not result['job_id']:
            raise ValueError()
    except (ValueError, TypeError, KeyError):
        raise HTTPException(503, 'tilda_hint_store_invalid_response') from None
    return PlainTextResponse('ok', headers={'Cache-Control': 'no-store'})


@router.get('/webhook/{binding}/{token}/published')
async def official_publish_callback(binding: str, token: str, projectid: str, pageid: str, published: str,
                                    publickey: str) -> PlainTextResponse:
    try:
        hint = PublishHint(binding=binding, token=token, projectid=projectid, pageid=pageid, published=published, publickey=publickey)
    except ValueError:
        raise HTTPException(422, 'tilda_hint_invalid') from None
    return await accept_hint(hint)


@router.post('/internal/tilda/hint', dependencies=[Depends(require_internal_key)])
async def normalized_hint(hint: PublishHint) -> PlainTextResponse:
    return await accept_hint(hint)


@router.post('/webhook/published')
def legacy_publish_callback() -> None:
    raise HTTPException(410, 'official_get_callback_required')
