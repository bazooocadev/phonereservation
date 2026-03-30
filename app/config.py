from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # Database
    database_url: str = "postgresql+asyncpg://redial:redialpass@localhost:5432/redialdb"
    database_url_sync: str = "postgresql://redial:redialpass@localhost:5432/redialdb"

    # App
    secret_key: str = "dev-secret-key"
    python_env: str = "development"

    # Twilio
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""

    # Telnyx
    telnyx_api_key: str = ""
    telnyx_connection_id: str = ""

    # Redial
    redial_interval_sec: float = 1.0
    dial_interval_sec: float = 3.0   # 回線間のダイアル間隔（秒）
    max_concurrent_calls: int = 30
    twilio_cost_per_min: float = 0.085  # USD/分（日本向けモバイル概算）

    # Webhook
    webhook_base_url: str = "http://localhost:8000"

    class Config:
        env_file = ".env"
        extra = "ignore"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
