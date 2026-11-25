import time
import uuid
from typing import Callable
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from config.logging_config import get_logger
from services.api_gateway.config import settings
from services.api_gateway.middleware.error_handler import unexpected_error_response
from shared.tilda_contract import safe_callback_path

logger = get_logger(__name__)


class LoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id
        
        start_time = time.time()
        
        client_host = request.client.host if request.client else "unknown"
        safe_path = safe_callback_path(request.url.path)
        
        logger.info(
            "Request started",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": safe_path,
                "client_host": client_host,
                "user_agent": request.headers.get("user-agent", "unknown"),
                "service": settings.SERVICE_NAME
            }
        )
        
        try:
            response = await call_next(request)
        except Exception as exc:
            response = unexpected_error_response(request, exc)

        process_time = time.time() - start_time
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Process-Time"] = str(process_time)
        logger.info(
            "Request completed",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": safe_path,
                "status_code": response.status_code,
                "process_time": process_time,
                "client_host": client_host,
                "service": settings.SERVICE_NAME,
            },
        )
        return response
