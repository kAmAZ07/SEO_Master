from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import uvicorn
import asyncio
import uuid

from app.core.config import settings
from app.core.logging import setup_logging, logger
from app.db.session import engine, SessionLocal
from app.api.endpoints import projects, tasks, hitl, internal
from app.events.consumers import start_consumers, stop_consumers
from app.events.publishers import check_rabbitmq_connection
from app.scheduler.beat import start_scheduler, stop_scheduler


setup_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Management Service")
    
    consumer_task = asyncio.create_task(start_consumers())
    
    scheduler_task = asyncio.create_task(start_scheduler())
    
    logger.info("Management Service started successfully")
    
    yield
    
    logger.info("Shutting down Management Service")
    
    await stop_consumers()
    consumer_task.cancel()
    
    await stop_scheduler()
    scheduler_task.cancel()
    
    logger.info("Management Service stopped")


app = FastAPI(
    title="Management Service",
    description="Orchestration, projects, tasks, HITL workflow, saga coordination",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.ENVIRONMENT != "production" else None,
    redoc_url="/redoc" if settings.ENVIRONMENT != "production" else None,
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def correlation_id_middleware(request: Request, call_next):
    correlation_id = request.headers.get("X-Correlation-ID", str(uuid.uuid4()))
    request.state.correlation_id = correlation_id
    
    response = await call_next(request)
    response.headers["X-Correlation-ID"] = correlation_id
    
    return response


@app.middleware("http")
async def logging_middleware(request: Request, call_next):
    logger.info(
        f"Request started",
        extra={
            "method": request.method,
            "path": request.url.path,
            "correlation_id": getattr(request.state, "correlation_id", None),
        }
    )
    
    response = await call_next(request)
    
    logger.info(
        f"Request completed",
        extra={
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "correlation_id": getattr(request.state, "correlation_id", None),
        }
    )
    
    return response


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(
        f"Unhandled exception: {str(exc)}",
        extra={
            "path": request.url.path,
            "correlation_id": getattr(request.state, "correlation_id", None),
        },
        exc_info=True,
    )
    
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "detail": "Internal server error",
            "correlation_id": getattr(request.state, "correlation_id", None),
        },
    )


@app.get("/health", status_code=status.HTTP_200_OK)
async def health_check():
    return {
        "status": "healthy",
        "service": "management-service",
        "version": "1.0.0",
    }


@app.get("/health/ready", status_code=status.HTTP_200_OK)
async def readiness_check():
    db_healthy = False
    rabbitmq_healthy = False
    
    db = SessionLocal()
    try:
        db.execute("SELECT 1")
        db_healthy = True
    except Exception as e:
        logger.error(f"Database health check failed: {e}")
    finally:
        db.close()
    
    try:
        rabbitmq_healthy = await check_rabbitmq_connection()
    except Exception as e:
        logger.error(f"RabbitMQ health check failed: {e}")
    
    if not db_healthy or not rabbitmq_healthy:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "status": "unhealthy",
                "database": "up" if db_healthy else "down",
                "rabbitmq": "up" if rabbitmq_healthy else "down",
            },
        )
    
    return {
        "status": "ready",
        "database": "up",
        "rabbitmq": "up",
    }


app.include_router(
    projects.router,
    prefix="/api/v1/projects",
    tags=["projects"],
)

app.include_router(
    tasks.router,
    prefix="/api/v1/tasks",
    tags=["tasks"],
)

app.include_router(
    hitl.router,
    prefix="/api/v1/hitl",
    tags=["hitl"],
)

app.include_router(
    internal.router,
    prefix="/internal",
    tags=["internal"],
)


if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.ENVIRONMENT == "development",
        log_level=settings.LOG_LEVEL.lower(),
    )
