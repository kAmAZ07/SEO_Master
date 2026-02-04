import os
from typing import Optional
from pydantic import BaseSettings, Field, validator


class Settings(BaseSettings):
    
    ENVIRONMENT: str = Field(default="production", env="ENVIRONMENT")
    SERVICE_NAME: str = Field(default="api-gateway", env="SERVICE_NAME")
    SERVICE_PORT: int = Field(default=8000, env="API_GATEWAY_PORT")
    
    DATABASE_URL: str = Field(..., env="DATABASE_URL")
    
    REDIS_URL: str = Field(..., env="REDIS_URL")
    REDIS_HOST: str = Field(default="localhost", env="REDIS_HOST")
    REDIS_PORT: int = Field(default=6379, env="REDIS_PORT")
    REDIS_PASSWORD: Optional[str] = Field(default=None, env="REDIS_PASSWORD")
    REDIS_DB: int = Field(default=0, env="REDIS_DB")
    
    LOG_LEVEL: str = Field(default="INFO", env="LOG_LEVEL")
    LOG_FORMAT: str = Field(default="json", env="LOG_FORMAT")
    LOG_FILE_PATH: str = Field(default="logs/api_gateway.log", env="LOG_FILE_PATH")
    
    CORS_ORIGINS: str = Field(default="*", env="CORS_ORIGINS")
    CORS_ALLOW_CREDENTIALS: bool = Field(default=True, env="CORS_ALLOW_CREDENTIALS")
    CORS_ALLOW_METHODS: str = Field(default="GET,POST,PUT,DELETE,OPTIONS", env="CORS_ALLOW_METHODS")
    CORS_ALLOW_HEADERS: str = Field(default="*", env="CORS_ALLOW_HEADERS")
    
    JWT_SECRET_KEY: str = Field(..., env="JWT_SECRET_KEY")
    JWT_ALGORITHM: str = Field(default="HS256", env="JWT_ALGORITHM")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(default=30, env="ACCESS_TOKEN_EXPIRE_MINUTES")
    REFRESH_TOKEN_EXPIRE_DAYS: int = Field(default=7, env="REFRESH_TOKEN_EXPIRE_DAYS")
    
    PUBLIC_RATE_LIMIT: int = Field(default=5, env="PUBLIC_RATE_LIMIT")
    PUBLIC_RATE_LIMIT_WINDOW_SECONDS: int = Field(default=3600, env="PUBLIC_RATE_LIMIT_WINDOW_SECONDS")
    
    PUBLIC_AUDIT_MAX_PAGES: int = Field(default=10, env="PUBLIC_AUDIT_MAX_PAGES")
    PUBLIC_AUDIT_TIMEOUT_SECONDS: int = Field(default=60, env="PUBLIC_AUDIT_TIMEOUT_SECONDS")
    PUBLIC_AUDIT_RETENTION_DAYS: int = Field(default=7, env="PUBLIC_AUDIT_RETENTION_DAYS")
    
    AUDIT_SERVICE_URL: str = Field(default="http://localhost:8001", env="AUDIT_SERVICE_URL")
    MANAGEMENT_SERVICE_URL: str = Field(default="http://localhost:8004", env="MANAGEMENT_SERVICE_URL")
    SEMANTIC_SERVICE_URL: str = Field(default="http://localhost:8002", env="SEMANTIC_SERVICE_URL")
    REPORTING_SERVICE_URL: str = Field(default="http://localhost:8003", env="REPORTING_SERVICE_URL")
    
    ENABLE_METRICS: bool = Field(default=True, env="ENABLE_METRICS")
    METRICS_PORT: int = Field(default=9100, env="METRICS_PORT")
    
    @validator("JWT_SECRET_KEY")
    def validate_jwt_secret(cls, v, values):
        if values.get("ENVIRONMENT") == "production" and len(v) < 32:
            raise ValueError("JWT_SECRET_KEY must be at least 32 characters in production")
        return v
    
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


def is_production():
    return settings.ENVIRONMENT.lower() == "production"


def is_development():
    return settings.ENVIRONMENT.lower() == "development"
