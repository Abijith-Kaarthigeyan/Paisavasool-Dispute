from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    APP_NAME: str = "Paisa Vasool Dispute Service"
    ENVIRONMENT: str = "local"

    DB_ASYNC_URL: str = "postgresql+asyncpg://paisavasool:paisavasool@postgres:5432/paisavasool"
    DB_SYNC_URL: str = "postgresql+psycopg2://paisavasool:paisavasool@postgres:5432/paisavasool"
    REDIS_URL: str = "redis://redis:6379/0"

    SECRET_KEY: str = "ilovepaisavasool"
    ALGORITHM: str = "HS256"

    AUTH_SERVICE_URL: str = "http://auth-service:8000"
    AR_SERVICE_URL: str = "http://ar-service:8001"

    GEMINI_API_KEY: str = ""
    GEMINI_MODEL_NAME: str = "gemini-2.5-flash"
    OPENROUTER_API_KEY: str = ""

    # SLA Durations (in Hours)
    DISPUTE_SLA_PAYMENT_HOURS: int = 12
    DISPUTE_SLA_AMENDMENT_HOURS: int = 24
    DISPUTE_SLA_OPERATIONAL_HOURS: int = 72

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
