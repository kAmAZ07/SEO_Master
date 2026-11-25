from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from datetime import datetime
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from config.logging_config import get_logger
from services.api_gateway.config import settings
from shared.tilda_contract import safe_callback_path

logger = get_logger(__name__)


def unexpected_error_response(request: Request, exc: Exception) -> JSONResponse:
    request_id = getattr(request.state, "request_id", "unknown")
    logger.error(
        "Unhandled exception",
        extra={
            "request_id": request_id,
            "error_type": type(exc).__name__,
            "path": safe_callback_path(request.url.path),
            "method": request.method,
            "service": settings.SERVICE_NAME,
        },
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"error": {
            "code": 500,
            "message": "Internal server error",
            "request_id": request_id,
            "timestamp": datetime.utcnow().isoformat(),
        }},
    )


def setup_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(Exception, unexpected_error_response)
    
    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        request_id = getattr(request.state, "request_id", "unknown")
        
        logger.warning(
            "HTTP exception",
            extra={
                "request_id": request_id,
                "status_code": exc.status_code,
                "detail": exc.detail,
                "path": safe_callback_path(request.url.path),
                "method": request.method,
                "service": settings.SERVICE_NAME
            }
        )
        
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.status_code,
                    "message": exc.detail,
                    "request_id": request_id,
                    "timestamp": datetime.utcnow().isoformat()
                }
            },
            # Retry-After, WWW-Authenticate и заголовки лимитов, заданные при выбросе исключения,
            # должны дойти до клиента (ТЗ §3.4).
            headers=getattr(exc, "headers", None),
        )
    
    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        request_id = getattr(request.state, "request_id", "unknown")
        
        errors = []
        for error in exc.errors():
            errors.append({
                "field": ".".join(str(loc) for loc in error["loc"]),
                "message": error["msg"],
                "type": error["type"]
            })
        
        logger.warning(
            "Validation error",
            extra={
                "request_id": request_id,
                "errors": errors,
                "path": safe_callback_path(request.url.path),
                "method": request.method,
                "service": settings.SERVICE_NAME
            }
        )
        
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "error": {
                    "code": 422,
                    "message": "Validation error",
                    "details": errors,
                    "request_id": request_id,
                    "timestamp": datetime.utcnow().isoformat()
                }
            }
        )
    
    @app.exception_handler(ValidationError)
    async def pydantic_validation_exception_handler(request: Request, exc: ValidationError):
        request_id = getattr(request.state, "request_id", "unknown")
        
        errors = []
        for error in exc.errors():
            errors.append({
                "field": ".".join(str(loc) for loc in error["loc"]),
                "message": error["msg"],
                "type": error["type"]
            })
        
        logger.warning(
            "Pydantic validation error",
            extra={
                "request_id": request_id,
                "errors": errors,
                "path": safe_callback_path(request.url.path),
                "method": request.method,
                "service": settings.SERVICE_NAME
            }
        )
        
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "error": {
                    "code": 422,
                    "message": "Data validation error",
                    "details": errors,
                    "request_id": request_id,
                    "timestamp": datetime.utcnow().isoformat()
                }
            }
        )
    
    @app.exception_handler(SQLAlchemyError)
    async def database_exception_handler(request: Request, exc: SQLAlchemyError):
        request_id = getattr(request.state, "request_id", "unknown")
        
        logger.error(
            "Database error",
            extra={
                "request_id": request_id,
                "error_type": type(exc).__name__,
                "path": safe_callback_path(request.url.path),
                "method": request.method,
                "service": settings.SERVICE_NAME
            }
        )
        
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": {
                    "code": 500,
                    "message": "Database error occurred",
                    "request_id": request_id,
                    "timestamp": datetime.utcnow().isoformat()
                }
            }
        )
