from fastapi import FastAPI

from config import settings
from internal_routes import router as internal_router
from tilda_webhook_handler import router as webhook_router

app = FastAPI(title='Tilda Adapter', version='0.2.0')
app.include_router(webhook_router)
app.include_router(internal_router)


@app.get('/health')
async def health() -> dict:
    return {'status': 'ok', 'service': 'tilda-adapter', 'port': settings.port, 'mock_mode': settings.mock_mode}


@app.get('/health/ready')
async def readiness() -> dict:
    if settings.mock_mode:
        return {
            'status': 'ready',
            'service': 'tilda-adapter',
            'mock_mode': True,
            'upstream': 'mock',
        }

    return {
        'status': 'ready',
        'service': 'tilda-adapter',
        'mock_mode': False,
        'upstream': 'per-request-credentials',
    }


if __name__ == '__main__':
    import uvicorn

    uvicorn.run('main:app', host='0.0.0.0', port=settings.port, reload=False)
