import os
from typing import Optional, List
from pydantic import BaseSettings, Field, validator


class Settings(BaseSettings):
    
    ENVIRONMENT: str = Field(default="production", env="ENVIRONMENT")
    SERVICE_NAME: str = Field(default="client-api-gateway", env="SERVICE_NAME")
    SERVICE_PORT: int = Field(default=8006, env="CLIENT_API_GATEWAY_PORT")
    
    DATABASE_URL: str = Field(..., env="DATABASE_URL")
    
    REDIS_URL: str = Field(..., env="REDIS_URL")
    REDIS_HOST: str = Field(default="localhost", env="REDIS_HOST")
    REDIS_PORT: int = Field(default=6379, env="REDIS_PORT")
    REDIS_PASSWORD: Optional[str] = Field(default=None, env="REDIS_PASSWORD")
    REDIS_DB: int = Field(default=0, env="REDIS_DB")
    
    LOG_LEVEL: str = Field(default="INFO", env="LOG_LEVEL")
    LOG_FORMAT: str = Field(default="json", env="LOG_FORMAT")
    LOG_FILE_PATH: str = Field(default="logs/client_api_gateway.log", env="LOG_FILE_PATH")
    
    INTERNAL_API_KEY: str = Field(
        default="internal-secret-key-change-in-production",
        env="INTERNAL_API_KEY"
    )
    
    HMAC_KEY_VERSION: int = Field(default=1, env="HMAC_KEY_VERSION")
    HMAC_KEY_ROTATION_DAYS: int = Field(default=90, env="HMAC_KEY_ROTATION_DAYS")
    HMAC_KEY_GRACE_PERIOD_DAYS: int = Field(default=7, env="HMAC_KEY_GRACE_PERIOD_DAYS")
    HMAC_SIGNATURE_MAX_AGE_SECONDS: int = Field(default=300, env="HMAC_SIGNATURE_MAX_AGE_SECONDS")
    
    CHANGELOG_RETENTION_DAYS: int = Field(default=365, env="CHANGELOG_RETENTION_DAYS")
    DEPLOYMENT_LOG_RETENTION_DAYS: int = Field(default=90, env="DEPLOYMENT_LOG_RETENTION_DAYS")
    
    ENABLE_SIGNATURE_VALIDATION: bool = Field(default=True, env="ENABLE_SIGNATURE_VALIDATION")
    ENABLE_IP_WHITELIST: bool = Field(default=False, env="ENABLE_IP_WHITELIST")
    ALLOWED_IPS: str = Field(default="", env="ALLOWED_IPS")
    
    ENABLE_METRICS: bool = Field(default=True, env="ENABLE_METRICS")
    METRICS_PORT: int = Field(default=9106, env="METRICS_PORT")
    
    PATCH_RATE_LIMIT_PER_PROJECT: int = Field(default=100, env="PATCH_RATE_LIMIT_PER_PROJECT")
    PATCH_RATE_LIMIT_WINDOW_SECONDS: int = Field(default=3600, env="PATCH_RATE_LIMIT_WINDOW_SECONDS")
    
    MAX_CHANGELOG_SIZE_MB: int = Field(default=10, env="MAX_CHANGELOG_SIZE_MB")
    MAX_PENDING_CHANGES_PER_PROJECT: int = Field(default=1000, env="MAX_PENDING_CHANGES_PER_PROJECT")
    
    @validator("ALLOWED_IPS")
    def validate_allowed_ips(cls, v):
        if not v:
            return []
        return [ip.strip() for ip in v.split(",") if ip.strip()]
    
    @validator("LOG_LEVEL")
    def validate_log_level(cls, v):
        allowed_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        v_upper = v.upper()
        if v_upper not in allowed_levels:
            raise ValueError(f"LOG_LEVEL must be one of {allowed_levels}")
        return v_upper
    
    @validator("LOG_FORMAT")
    def validate_log_format(cls, v):
        allowed_formats = ["json", "text"]
        v_lower = v.lower()
        if v_lower not in allowed_formats:
            raise ValueError(f"LOG_FORMAT must be one of {allowed_formats}")
        return v_lower
    
    @validator("INTERNAL_API_KEY")
    def validate_internal_key(cls, v, values):
        env = values.get("ENVIRONMENT", "production")
        if env.lower() == "production" and v == "internal-secret-key-change-in-production":
            raise ValueError("INTERNAL_API_KEY must be changed in production")
        if len(v) < 32:
            raise ValueError("INTERNAL_API_KEY must be at least 32 characters")
        return v
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True


settings = Settings()


def get_redis_config():
    return {
        "host": settings.REDIS_HOST,
        "port": settings.REDIS_PORT,
        "password": settings.REDIS_PASSWORD,
        "db": settings.REDIS_DB,
        "url": settings.REDIS_URL
    }


def get_database_config():
    return {
        "url": settings.DATABASE_URL
    }


def get_hmac_config():
    return {
        "key_version": settings.HMAC_KEY_VERSION,
        "rotation_days": settings.HMAC_KEY_ROTATION_DAYS,
        "grace_period_days": settings.HMAC_KEY_GRACE_PERIOD_DAYS,
        "signature_max_age": settings.HMAC_SIGNATURE_MAX_AGE_SECONDS
    }


def get_changelog_config():
    return {
        "retention_days": settings.CHANGELOG_RETENTION_DAYS,
        "deployment_retention_days": settings.DEPLOYMENT_LOG_RETENTION_DAYS,
        "max_size_mb": settings.MAX_CHANGELOG_SIZE_MB
    }


def get_rate_limit_config():
    return {
        "per_project": settings.PATCH_RATE_LIMIT_PER_PROJECT,
        "window_seconds": settings.PATCH_RATE_LIMIT_WINDOW_SECONDS
    }


def is_production():
    return settings.ENVIRONMENT.lower() == "production"


def is_development():
    return settings.ENVIRONMENT.lower() == "development"
