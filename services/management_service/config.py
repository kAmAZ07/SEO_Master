from typing import Annotated, List, Optional

from pydantic import Field, PostgresDsn, RedisDsn, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file='.env',
        env_file_encoding='utf-8',
        case_sensitive=True,
        extra='ignore',
    )

    ENVIRONMENT: str = 'development'

    HOST: str = '0.0.0.0'
    PORT: int = 8004

    LOG_LEVEL: str = 'INFO'

    DATABASE_URL: PostgresDsn
    REDIS_URL: RedisDsn

    RABBITMQ_HOST: str = 'localhost'
    RABBITMQ_PORT: int = 5672
    RABBITMQ_USER: str = 'guest'
    RABBITMQ_PASSWORD: str = 'guest'
    RABBITMQ_VHOST: str = '/'

    AUDIT_SERVICE_URL: str = 'http://localhost:8001'
    SEMANTIC_SERVICE_URL: str = 'http://localhost:8002'
    REPORTING_SERVICE_URL: str = 'http://localhost:8003'
    CLIENT_GATEWAY_URL: str = 'http://localhost:8005'

    CORS_ORIGINS: Annotated[List[str], NoDecode] = Field(
        default=['http://localhost:3000', 'http://localhost:5173'],
    )

    INTERNAL_API_KEY: str

    DEFAULT_CRAWL_SCHEDULE: str = '0 2 * * *'
    DEFAULT_FFSCORE_SCHEDULE: str = '0 3 * * *'

    TASK_PRIORITY_IMPACT_WEIGHT: float = 0.6
    TASK_PRIORITY_URGENCY_WEIGHT: float = 0.3
    TASK_PRIORITY_EFFORT_WEIGHT: float = 0.1

    HITL_AUTO_APPROVE_LOW_RISK: bool = False
    HITL_TIMEOUT_HOURS: int = 72

    MAX_CONCURRENT_TASKS_PER_PROJECT: int = 5

    SAGA_TIMEOUT_MINUTES: int = 30
    SAGA_RETRY_MAX_ATTEMPTS: int = 3

    CELERY_BROKER_URL: Optional[str] = None
    CELERY_RESULT_BACKEND: Optional[str] = None
    CELERY_TASK_ALWAYS_EAGER: bool = False

    SERVICE_REQUEST_TIMEOUT: int = 30
    SERVICE_REQUEST_RETRIES: int = 3

    @field_validator('CORS_ORIGINS', mode='before')
    @classmethod
    def parse_cors_origins(cls, value):
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(',') if origin.strip()]
        return value

    @field_validator('CELERY_BROKER_URL', mode='before')
    @classmethod
    def build_celery_broker_url(cls, value, info):
        if value:
            return value
        data = info.data
        return f"amqp://{data['RABBITMQ_USER']}:{data['RABBITMQ_PASSWORD']}@{data['RABBITMQ_HOST']}:{data['RABBITMQ_PORT']}/{data['RABBITMQ_VHOST']}"

    @field_validator('CELERY_RESULT_BACKEND', mode='before')
    @classmethod
    def build_celery_result_backend(cls, value, info):
        if value:
            return value
        redis_url = info.data.get('REDIS_URL')
        if redis_url:
            return str(redis_url)
        return 'rpc://'

    @property
    def rabbitmq_url(self) -> str:
        return f"amqp://{self.RABBITMQ_USER}:{self.RABBITMQ_PASSWORD}@{self.RABBITMQ_HOST}:{self.RABBITMQ_PORT}/{self.RABBITMQ_VHOST}"


settings = Settings()
