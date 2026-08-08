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

    # ── Admin session auth ────────────────────────────────────────────────────
    # The cookie is host-only and the API shares a registrable domain with the
    # site (api.gabrielmayorga.dev / gabrielmayorga.dev), so requests from the
    # dashboard are same-site and SameSite=Lax is enough — no SameSite=None, and
    # nothing readable by JavaScript.
    session_cookie_name: str = "gabox_session"
    csrf_cookie_name: str = "gabox_csrf"
    # Browsers treat localhost as a secure context, so this can stay on for
    # local development. Tests turn it off because their base URL is plain http
    # on a non-localhost host, and httpx correctly withholds Secure cookies.
    session_cookie_secure: bool = True
    session_cookie_samesite: str = "lax"
    # Absolute ceiling on a session's life, refresh or not.
    session_max_age_seconds: int = 60 * 60 * 12
    # Inactivity cutoff, slid forward on each authenticated request.
    session_idle_seconds: int = 60 * 60 * 2

    # ── Login throttling ──────────────────────────────────────────────────────
    login_max_attempts: int = 5
    login_attempt_window_seconds: int = 60 * 15

    @field_validator("session_cookie_samesite")
    @classmethod
    def _check_samesite(cls, value: str) -> str:
        allowed = {"lax", "strict", "none"}
        normalised = value.lower()
        if normalised not in allowed:
            raise ValueError(f"session_cookie_samesite must be one of {sorted(allowed)}")
        return normalised

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
