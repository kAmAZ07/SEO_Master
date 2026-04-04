from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', env_prefix='TILDA_', extra='ignore')

    port: int = 8010
    base_url: str = Field(default='https://api.tildacdn.info/v1')
    public_key: str | None = None
    secret_key: str | None = None

    internal_api_key: str | None = None
    webhook_secret: str | None = None
    request_timeout_seconds: float = 20.0
    mock_mode: bool = False


settings = Settings()
