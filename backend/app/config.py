from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "NovaFlow"
    secret_key: str = "development-only-change-me"
    database_url: str = "sqlite:///./novaflow.db"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    demo_seed: bool = True
    access_token_minutes: int = 480
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
