import os
from typing import Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file='.env',
        env_file_encoding='utf-8',
        case_sensitive=True,
        extra='ignore',
    )

    ENVIRONMENT: str = 'production'
    SERVICE_NAME: str = 'client-api-gateway'
    SERVICE_PORT: int = 8005

    DATABASE_URL: str

    LOG_LEVEL: str = 'INFO'

    INTERNAL_API_KEY: Optional[str] = None

    HMAC_MAX_DRIFT_SECONDS: int = 300
    HMAC_ROTATION_DAYS: int = 90
    HMAC_GRACE_DAYS: int = 7

    CLIENT_API_HMAC_KEYS_JSON: Optional[str] = None
    CLIENT_API_RATE_LIMIT_PER_PROJECT: int = 100
    CLIENT_API_RATE_LIMIT_WINDOW_SECONDS: int = 3600
    CLIENT_API_IP_WHITELIST: str = ''
    CLIENT_API_TRUST_PROXY_HEADERS: bool = False

    WORDPRESS_BASE_URL: Optional[str] = None
    WORDPRESS_HMAC_SECRET: Optional[str] = None

    TILDA_PUBLIC_KEY: Optional[str] = None
    TILDA_SECRET_KEY: Optional[str] = None
    TILDA_PROJECT_ID: Optional[str] = None
    TILDA_ADAPTER_URL: Optional[str] = None
    TILDA_INTERNAL_API_KEY: Optional[str] = None

    MANAGEMENT_SERVICE_URL: str = 'http://localhost:8004'

    CORS_ORIGINS: str = '*'
    CORS_ALLOW_CREDENTIALS: bool = True
    CORS_ALLOW_METHODS: str = 'GET,POST,PATCH,OPTIONS'
    CORS_ALLOW_HEADERS: str = '*'

    @field_validator('SERVICE_PORT', mode='before')
    @classmethod
    def resolve_service_port(cls, value):
        if os.getenv('SERVICE_PORT') in (None, ''):
            alt = os.getenv('CLIENT_API_GATEWAY_PORT')
            if alt:
                return int(alt)
        return value

    @field_validator('LOG_LEVEL')
    @classmethod
    def validate_log_level(cls, value: str) -> str:
        allowed_levels = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
        value_upper = value.upper()
        if value_upper not in allowed_levels:
            raise ValueError(f'LOG_LEVEL must be one of {allowed_levels}')
        return value_upper


settings = Settings()


def is_development() -> bool:
    return settings.ENVIRONMENT.lower() == 'development'


def is_production() -> bool:
    return settings.ENVIRONMENT.lower() == 'production'
