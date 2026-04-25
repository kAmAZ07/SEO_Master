from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="REPORTING_", extra="ignore")

    port: int = 8003

    database_url: str = Field(default="postgresql+asyncpg://postgres:postgres@postgres:5432/reporting_db")
    auto_create_tables: bool = False
    rabbitmq_url: str | None = None
    redis_url: str | None = "redis://redis:6379/0"


settings = Settings()
