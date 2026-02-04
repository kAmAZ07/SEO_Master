from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import sys
import os

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))))

from services.api_gateway.config import settings


def setup_cors(app: FastAPI) -> None:
    cors_origins = settings.CORS_ORIGINS
    
    if isinstance(cors_origins, str):
        if cors_origins == "*":
            origins = ["*"]
        else:
            origins = [origin.strip() for origin in cors_origins.split(",") if origin.strip()]
    else:
        origins = cors_origins
    
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=settings.CORS_ALLOW_CREDENTIALS,
        allow_methods=settings.CORS_ALLOW_METHODS.split(",") if isinstance(settings.CORS_ALLOW_METHODS, str) else settings.CORS_ALLOW_METHODS,
        allow_headers=settings.CORS_ALLOW_HEADERS.split(",") if isinstance(settings.CORS_ALLOW_HEADERS, str) and settings.CORS_ALLOW_HEADERS != "*" else ["*"],
        expose_headers=["X-Request-ID", "X-Process-Time"],
        max_age=3600
    )
