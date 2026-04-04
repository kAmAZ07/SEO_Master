from fastapi import FastAPI, HTTPException

from config import settings
from internal_routes import router as internal_router
from tilda_api_client import TildaAPIClient
from tilda_webhook_handler import router as webhook_router

app = FastAPI(title='Tilda Adapter', version='0.2.0')
app.include_router(webhook_router)
app.include_router(internal_router)


@app.get('/health')
async def health() -> dict:
    return {'status': 'ok', 'service': 'tilda-adapter', 'port': settings.port, 'mock_mode': settings.mock_mode}


@app.get('/health/ready')
async def readiness() -> dict:
    client = TildaAPIClient()
    try:
        result = await client.validate_credentials()
        return {
            'status': 'ready',
            'service': 'tilda-adapter',
            'mock_mode': settings.mock_mode,
            'upstream': result.get('status'),
        }
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f'tilda_upstream_unavailable: {exc}') from exc


if __name__ == '__main__':
    import uvicorn

    uvicorn.run('main:app', host='0.0.0.0', port=settings.port, reload=False)
