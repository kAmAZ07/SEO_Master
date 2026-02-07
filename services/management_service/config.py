from pydantic_settings import BaseSettings
from pydantic import Field, PostgresDsn, RedisDsn, field_validator
from typing import List, Optional


class Settings(BaseSettings):
    ENVIRONMENT: str = Field(default="development", env="ENVIRONMENT")
    
    HOST: str = Field(default="0.0.0.0", env="HOST")
    PORT: int = Field(default=8004, env="PORT")
    
    LOG_LEVEL: str = Field(default="INFO", env="LOG_LEVEL")
    
    DATABASE_URL: PostgresDsn = Field(..., env="DATABASE_URL")
    
    REDIS_URL: RedisDsn = Field(..., env="REDIS_URL")
    
    RABBITMQ_HOST: str = Field(default="localhost", env="RABBITMQ_HOST")
    RABBITMQ_PORT: int = Field(default=5672, env="RABBITMQ_PORT")
    RABBITMQ_USER: str = Field(default="guest", env="RABBITMQ_USER")
    RABBITMQ_PASSWORD: str = Field(default="guest", env="RABBITMQ_PASSWORD")
    RABBITMQ_VHOST: str = Field(default="/", env="RABBITMQ_VHOST")
    
    AUDIT_SERVICE_URL: str = Field(default="http://localhost:8001", env="AUDIT_SERVICE_URL")
    SEMANTIC_SERVICE_URL: str = Field(default="http://localhost:8002", env="SEMANTIC_SERVICE_URL")
    REPORTING_SERVICE_URL: str = Field(default="http://localhost:8004", env="REPORTING_SERVICE_URL")
    CLIENT_GATEWAY_URL: str = Field(default="http://localhost:8006", env="CLIENT_GATEWAY_URL")
    
    CORS_ORIGINS: List[str] = Field(
        default=["http://localhost:3000", "http://localhost:5173"],
        env="CORS_ORIGINS"
    )
    
    INTERNAL_API_KEY: str = Field(..., env="INTERNAL_API_KEY")
    
    DEFAULT_CRAWL_SCHEDULE: str = Field(default="0 2 * * *", env="DEFAULT_CRAWL_SCHEDULE")
    DEFAULT_FFSCORE_SCHEDULE: str = Field(default="0 3 * * *", env="DEFAULT_FFSCORE_SCHEDULE")
    
    TASK_PRIORITY_IMPACT_WEIGHT: float = Field(default=0.6, env="TASK_PRIORITY_IMPACT_WEIGHT")
    TASK_PRIORITY_URGENCY_WEIGHT: float = Field(default=0.3, env="TASK_PRIORITY_URGENCY_WEIGHT")
    TASK_PRIORITY_EFFORT_WEIGHT: float = Field(default=0.1, env="TASK_PRIORITY_EFFORT_WEIGHT")
    
    HITL_AUTO_APPROVE_LOW_RISK: bool = Field(default=False, env="HITL_AUTO_APPROVE_LOW_RISK")
    HITL_TIMEOUT_HOURS: int = Field(default=72, env="HITL_TIMEOUT_HOURS")
    
    MAX_CONCURRENT_TASKS_PER_PROJECT: int = Field(default=5, env="MAX_CONCURRENT_TASKS_PER_PROJECT")
    
    SAGA_TIMEOUT_MINUTES: int = Field(default=30, env="SAGA_TIMEOUT_MINUTES")
    SAGA_RETRY_MAX_ATTEMPTS: int = Field(default=3, env="SAGA_RETRY_MAX_ATTEMPTS")
    
    CELERY_BROKER_URL: Optional[str] = Field(default=None, env="CELERY_BROKER_URL")
    CELERY_RESULT_BACKEND: Optional[str] = Field(default=None, env="CELERY_RESULT_BACKEND")
    CELERY_TASK_ALWAYS_EAGER: bool = Field(default=False, env="CELERY_TASK_ALWAYS_EAGER")
    
    SERVICE_REQUEST_TIMEOUT: int = Field(default=30, env="SERVICE_REQUEST_TIMEOUT")
    SERVICE_REQUEST_RETRIES: int = Field(default=3, env="SERVICE_REQUEST_RETRIES")
    
    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def parse_cors_origins(cls, v):
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(",")]
        return v
    
    @field_validator("CELERY_BROKER_URL", mode="before")
    @classmethod
    def build_celery_broker_url(cls, v, info):
        if v:
            return v
        data = info.data
        return f"amqp://{data['RABBITMQ_USER']}:{data['RABBITMQ_PASSWORD']}@{data['RABBITMQ_HOST']}:{data['RABBITMQ_PORT']}/{data['RABBITMQ_VHOST']}"
    
    @field_validator("CELERY_RESULT_BACKEND", mode="before")
    @classmethod
    def build_celery_result_backend(cls, v, info):
        if v:
            return v
        redis_url = info.data.get("REDIS_URL")
        if redis_url:
            return str(redis_url)
        return "rpc://"
    
    @property
    def rabbitmq_url(self) -> str:
        return f"amqp://{self.RABBITMQ_USER}:{self.RABBITMQ_PASSWORD}@{self.RABBITMQ_HOST}:{self.RABBITMQ_PORT}/{self.RABBITMQ_VHOST}"
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True


settings = Settings()
