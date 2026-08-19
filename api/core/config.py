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
    # Vite claims the next free port when 5173 is taken, so a second dev server
    # lands on 5174 or 5175 and its requests would otherwise be refused by CORS
    # with no hint as to why. Localhost only — nothing here widens the
    # production surface.
    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://localhost:5174",
        "http://localhost:5175",
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

    # Send HSTS. On by default because every deployment is HTTPS behind Vercel.
    security_hsts: bool = True

    # Whether X-Forwarded-For may be believed when identifying the caller.
    # True is correct behind a proxy that overwrites it, as Vercel does. Running
    # the API directly exposed, an attacker could forge the header to sidestep
    # the login lockout, so it must be turned off in that case.
    trust_proxy_headers: bool = True

    # ── Login throttling ──────────────────────────────────────────────────────
    login_max_attempts: int = 5
    login_attempt_window_seconds: int = 60 * 15

    # ── StatPitch ─────────────────────────────────────────────────────────────
    # All optional: without them the StatPitch routes return 503 and the rest of
    # the app is unaffected.
    statpitch_base_url: str = "https://statpitch-api.onrender.com"
    # The free Render instance sleeps after ~15 minutes idle and the next call
    # pays a cold start of tens of seconds. This is not a value to shrink.
    statpitch_timeout_seconds: float = 60.0

    # Which competitions to sync. Defaults to the five with an odds market we
    # can price against; the seven cups get predictions but never a bet.
    statpitch_competitions: list[str] = [
        "ENG.PL",
        "ESP.LALIGA",
        "GER.BUNDESLIGA",
        "ITA.SERIEA",
        "FRA.LIGUE1",
    ]

    # The day the frontend calls "today" rolls over at local midnight here.
    statpitch_timezone: str = "America/Managua"
    # Days kept either side of today, so 1 means yesterday/today/tomorrow.
    statpitch_retention_days: int = 1

    odds_api_key: str = ""
    odds_api_region: str = "eu"
    # One request per market per league per run, against a 500/month free tier.
    # h2h alone across five leagues is ~150/month; adding totals and btts takes
    # it to ~450 before scores are counted.
    odds_api_markets: list[str] = ["h2h"]
    # Empty means average every bookmaker the region returns.
    odds_api_bookmakers: list[str] = []

    @field_validator(
        "statpitch_competitions",
        "odds_api_markets",
        "odds_api_bookmakers",
        mode="before",
    )
    @classmethod
    def _split_csv(cls, value: object) -> object:
        """Accept `a,b` in addition to pydantic's default JSON list."""
        if isinstance(value, str) and not value.strip().startswith("["):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("statpitch_timezone")
    @classmethod
    def _check_timezone(cls, value: str) -> str:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"statpitch_timezone is not a known IANA zone: {value!r}") from exc
        return value

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
