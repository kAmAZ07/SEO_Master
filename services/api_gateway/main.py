import sys
import os
from fastapi import FastAPI
from fastapi.responses import JSONResponse

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.api_gateway.config import settings
from services.api_gateway.middleware import setup_cors, LoggingMiddleware, setup_error_handlers
from services.api_gateway.routes import health_router
from config.logging_config import get_logger

logger = get_logger(__name__)

app = FastAPI(
    title="API Gateway",
    description="Main API Gateway for user authentication and public audit",
    version="1.0.0",
    docs_url="/docs" if settings.is_development() else None,
    redoc_url="/redoc" if settings.is_development() else None
)


setup_cors(app)
app.add_middleware(LoggingMiddleware)
setup_error_handlers(app)


app.include_router(health_router)


@app.on_event("startup")
async def startup_event():
    logger.info(
        f"Starting {settings.SERVICE_NAME}",
        extra={
            "environment": settings.ENVIRONMENT,
            "port": settings.SERVICE_PORT
        }
    )


@app.on_event("shutdown")
async def shutdown_event():
    logger.info(f"Shutting down {settings.SERVICE_NAME}")


@app.get("/")
async def root():
    return {
        "service": settings.SERVICE_NAME,
        "version": "1.0.0",
        "status": "running",
        "environment": settings.ENVIRONMENT
    }


if __name__ == "__main__":
    import uvicorn
    
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=settings.SERVICE_PORT,
        reload=settings.is_development(),
        log_level=settings.LOG_LEVEL.lower()
    )
