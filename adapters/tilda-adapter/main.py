import logging
from fastapi import FastAPI, HTTPException
from shared.tilda_contract import CallbackAccessFilter, capabilities

from config import settings
from internal_routes import router as internal_router
from tilda_webhook_handler import router as webhook_router

app = FastAPI(title='Tilda Adapter', version='0.2.0')
logging.getLogger('uvicorn.access').addFilter(CallbackAccessFilter())
app.include_router(webhook_router)
app.include_router(internal_router)


@app.get('/health')
async def health() -> dict[str, object]:
    return {'status': 'ok', 'service': 'tilda-adapter', 'port': settings.port, 'capabilities': capabilities()}


@app.get('/health/ready')
async def readiness() -> dict[str, object]:
    if settings.mock_mode or len(settings.internal_api_key or '') < 32:
        raise HTTPException(503, 'tilda_read_transport_not_ready')
    return {
        'status': 'ready',
        'service': 'tilda-adapter',
        'upstream': 'not_checked',
        'capabilities': capabilities(),
        'read_enabled': not settings.mock_mode,
    }


if __name__ == '__main__':
    import uvicorn

    uvicorn.run('main:app', host='0.0.0.0', port=settings.port, reload=False)
