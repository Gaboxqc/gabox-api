"""Typed application settings, resolved once from the environment.

Every configuration value the app needs is declared here. Missing required
variables raise at import time with a message naming them, so a
misconfigured deployment fails on boot instead of at the first request.
"""

import os
from functools import lru_cache

from pydantic import ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Required ──────────────────────────────────────────────────────────────
    database_url: str
    api_master_key: str

    # ── Optional ──────────────────────────────────────────────────────────────
    sql_echo: bool = False
    log_level: str = "INFO"
    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://localhost:8000",
        "https://gabrielmayorga.dev",
        "https://www.gabrielmayorga.dev",
    ]

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        """Accept `a.com,b.com` in addition to pydantic's default JSON list."""
        if isinstance(value, str) and not value.strip().startswith("["):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @property
    def is_serverless(self) -> bool:
        """Vercel sets VERCEL=1 on every deployment, preview builds included."""
        return bool(os.getenv("VERCEL"))


@lru_cache
def get_settings() -> Settings:
    try:
        return Settings()
    except ValidationError as exc:
        missing = sorted(
            str(error["loc"][0]).upper() for error in exc.errors() if error["type"] == "missing"
        )
        if missing:
            raise RuntimeError(
                f"Missing required environment variable(s): {', '.join(missing)}. "
                "Copy .env.example to .env and fill them in."
            ) from exc
        raise


settings = get_settings()
