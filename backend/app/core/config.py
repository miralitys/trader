from __future__ import annotations

from functools import lru_cache
from typing import List

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


DEFAULT_UNIVERSE_INPUT = [
    "DYDX",
    "INJ",
    "ICP",
    "GALA",
    "AXS",
    "TRB",
    "ONDO",
    "IOTA",
    "NOT",
    "FIL",
    "NEO",
    "ENJ",
    "HYPE",
    "STRK",
    "SLP",
    "ONE",
    "MINA",
    "RVN",
    "RUNE",
]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Trader Control Panel"
    environment: str = "dev"
    database_url: str = Field(
        default="postgresql+psycopg2://trader:trader@postgres:5432/trader",
        alias="DATABASE_URL",
    )
    redis_url: str = Field(default="redis://redis:6379/0", alias="REDIS_URL")

    jwt_secret: str = Field(default="change-me", alias="JWT_SECRET")
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = Field(default=1440, alias="JWT_EXPIRE_MINUTES")

    coinbase_api_base_url: str = Field(
        default="https://api.coinbase.com", alias="COINBASE_API_BASE_URL"
    )
    coinbase_api_key: str = Field(default="", alias="COINBASE_API_KEY")
    coinbase_api_secret: str = Field(default="", alias="COINBASE_API_SECRET")
    coinbase_api_passphrase: str = Field(default="", alias="COINBASE_API_PASSPHRASE")

    paper_enabled: bool = Field(default=True, alias="PAPER_ENABLED")
    live_enabled: bool = Field(default=False, alias="LIVE_ENABLED")

    backend_cors_origins: str = Field(
        default="http://localhost:3000", alias="BACKEND_CORS_ORIGINS"
    )
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    sentry_dsn: str = Field(default="", alias="SENTRY_DSN")

    secret_encryption_key: str = Field(default="", alias="SECRET_ENCRYPTION_KEY")

    maker_fee_pct: float = 0.4
    taker_fee_pct: float = 0.6
    market_exit_slippage_pct: float = 0.05

    backfill_5m_days: int = Field(default=180, alias="BACKFILL_5M_DAYS")
    backfill_15m_days: int = Field(default=365, alias="BACKFILL_15M_DAYS")
    backfill_1h_days: int = Field(default=730, alias="BACKFILL_1H_DAYS")
    backfill_max_symbols_per_run: int = Field(
        default=3, alias="BACKFILL_MAX_SYMBOLS_PER_RUN"
    )
    backfill_max_chunks_per_tf: int = Field(
        default=6, alias="BACKFILL_MAX_CHUNKS_PER_TF"
    )

    def cors_origins_list(self) -> List[str]:
        return [item.strip() for item in self.backend_cors_origins.split(",") if item.strip()]

    @property
    def normalized_database_url(self) -> str:
        """
        Render Postgres commonly provides URLs with the legacy postgres:// scheme.
        SQLAlchemy expects postgresql+psycopg2:// for this project.
        """
        url = self.database_url.strip()
        if url.startswith("postgres://"):
            return url.replace("postgres://", "postgresql+psycopg2://", 1)
        if url.startswith("postgresql://") and "+psycopg2" not in url:
            return url.replace("postgresql://", "postgresql+psycopg2://", 1)
        return url


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
