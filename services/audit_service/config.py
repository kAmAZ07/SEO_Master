from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="AUDIT_", extra="ignore")

    port: int = 8001

    database_url: str = Field(default="postgresql+asyncpg://postgres:postgres@postgres:5432/audit_db")
    rabbitmq_url: str | None = Field(default=None)

    psi_api_key: str | None = Field(default=None)

    gsc_credentials_json: str | None = Field(default=None)
    gsc_token_json: str | None = Field(default=None)

    user_agent: str = Field(default="SEO-Master-AuditBot/1.0")
    default_timeout_s: float = Field(default=10.0)
    max_internal_link_checks: int = Field(default=50)


settings = Settings()