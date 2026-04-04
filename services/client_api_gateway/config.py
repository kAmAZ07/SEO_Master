import os
from typing import Optional
from pydantic import BaseSettings, Field, validator


class Settings(BaseSettings):

    ENVIRONMENT: str = Field(default="production", env="ENVIRONMENT")
    SERVICE_NAME: str = Field(default="client-api-gateway", env="SERVICE_NAME")
    SERVICE_PORT: int = Field(default=8005, env="SERVICE_PORT")

    DATABASE_URL: str = Field(..., env="DATABASE_URL")

    LOG_LEVEL: str = Field(default="INFO", env="LOG_LEVEL")

    INTERNAL_API_KEY: Optional[str] = Field(default=None, env="INTERNAL_API_KEY")

    HMAC_MAX_DRIFT_SECONDS: int = Field(default=300, env="HMAC_MAX_DRIFT_SECONDS")
    HMAC_ROTATION_DAYS: int = Field(default=90, env="HMAC_ROTATION_DAYS")
    HMAC_GRACE_DAYS: int = Field(default=7, env="HMAC_GRACE_DAYS")

    CLIENT_API_HMAC_SECRET: Optional[str] = Field(default=None, env="CLIENT_API_HMAC_SECRET")
    CLIENT_API_HMAC_PROJECT_ID: Optional[str] = Field(default=None, env="CLIENT_API_HMAC_PROJECT_ID")

    WORDPRESS_BASE_URL: Optional[str] = Field(default=None, env="WORDPRESS_BASE_URL")
    WORDPRESS_HMAC_SECRET: Optional[str] = Field(default=None, env="WORDPRESS_HMAC_SECRET")

    TILDA_ADAPTER_URL: Optional[str] = Field(default=None, env="TILDA_ADAPTER_URL")
    TILDA_INTERNAL_API_KEY: Optional[str] = Field(default=None, env="TILDA_INTERNAL_API_KEY")

    CORS_ORIGINS: str = Field(default="*", env="CORS_ORIGINS")
    CORS_ALLOW_CREDENTIALS: bool = Field(default=True, env="CORS_ALLOW_CREDENTIALS")
    CORS_ALLOW_METHODS: str = Field(default="GET,POST,PATCH,OPTIONS", env="CORS_ALLOW_METHODS")
    CORS_ALLOW_HEADERS: str = Field(default="*", env="CORS_ALLOW_HEADERS")

    @validator("SERVICE_PORT", pre=True)
    def resolve_service_port(cls, v):
        if os.getenv("SERVICE_PORT") in (None, ""):
            alt = os.getenv("CLIENT_API_GATEWAY_PORT")
            if alt:
                return int(alt)
        return v

    @validator("LOG_LEVEL")
    def validate_log_level(cls, v):
        allowed_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        v_upper = v.upper()
        if v_upper not in allowed_levels:
            raise ValueError(f"LOG_LEVEL must be one of {allowed_levels}")
        return v_upper

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True


settings = Settings()


def is_development() -> bool:
    return settings.ENVIRONMENT.lower() == "development"


def is_production() -> bool:
    return settings.ENVIRONMENT.lower() == "production"
