from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="SEMANTIC_", extra="ignore")

    port: int = 8002

    database_url: str = Field(default="postgresql+asyncpg://postgres:postgres@postgres:5432/semantic_db")
    auto_create_tables: bool = False
    rabbitmq_url: str | None = None

    redis_url: str | None = "redis://redis:6379/0"

    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"

    gemini_api_key: str | None = None
    gemini_model: str = "gemini-1.5-flash"

    llm_timeout_s_simple: float = 5.0
    llm_timeout_s_complex: float = 15.0
    llm_cache_ttl_seconds: int = 7 * 24 * 60 * 60

    user_agent: str = "SEO-Master-SemanticBot/1.0"


settings = Settings()
