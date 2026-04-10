from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="REPORTING_", extra="ignore")

    port: int = 8003

    database_url: str = Field(default="postgresql+asyncpg://postgres:postgres@postgres:5432/reporting_db")
    auto_create_tables: bool = False
    rabbitmq_url: str | None = None

    gsc_credentials_json: str | None = None
    gsc_token_json: str | None = None

    ga4_property_id: str | None = None
    ga4_credentials_json: str | None = None

    yandex_token: str | None = None


settings = Settings()
