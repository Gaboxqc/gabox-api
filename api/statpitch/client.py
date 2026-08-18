"""HTTP client for the StatPitch prediction service.

StatPitch stores nothing — it is stateless, has no database and no memory
between requests. This API is what persists its output, so everything here is
read-only and every call is a batch.

Two operational facts shape the whole module. The free Render instance sleeps
after ~15 minutes idle, so the first call pays a cold start of tens of seconds
and timeouts are sized in minutes rather than seconds. And a refusal arrives as
a well-formed 200, not an error, so `raise_for_status` is never enough on its
own.
"""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date

import httpx

from api.core.config import settings
from api.statpitch.models import SPFixture, SPFixturesPage, SPHealth

log = logging.getLogger("statpitch.client")

# StatPitch caps `limit` at 1000; 200 is its own documented batch size and
# keeps a cold-start response from getting unwieldy.
_PAGE_SIZE = 200
# Enough pages to cover the whole fixture list even if `total` is misreported.
_MAX_PAGES = 25


class StatPitchError(RuntimeError):
    """StatPitch could not be reached, or answered in a way we cannot use."""


class StatPitchRefusal(StatPitchError):
    """StatPitch declined at the endpoint level, citing a reason code.

    Distinct from an empty result: `NO_FIXTURE_SOURCE` means the fixture
    artifact is not loaded upstream, which is a broken deploy, not a quiet day.
    """

    def __init__(self, reason_code: str | None, reason: str | None) -> None:
        self.reason_code = reason_code or "UNKNOWN"
        self.reason = reason or "StatPitch declined without giving a reason."
        super().__init__(f"{self.reason_code}: {self.reason}")


@dataclass
class FixtureWindow:
    """Everything one window fetch produced, warnings included."""

    fixtures: list[SPFixture] = field(default_factory=list)
    model_version: str | None = None
    config_version: str | None = None
    generated_at_source: str | None = None
    warnings: list[str] = field(default_factory=list)


def _base_url() -> str:
    url = settings.statpitch_base_url.strip().rstrip("/")
    if not url:
        raise StatPitchError("STATPITCH_BASE_URL is not configured on the server.")
    return url


def build_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=_base_url(),
        timeout=httpx.Timeout(settings.statpitch_timeout_seconds),
        headers={"accept": "application/json"},
    )


async def _get_json(client: httpx.AsyncClient, path: str, params: dict | None = None) -> dict:
    """GET with one retry, because the retry after a cold start is milliseconds."""
    last_error: Exception | None = None

    for attempt in range(2):
        try:
            response = await client.get(path, params=params)
            response.raise_for_status()
            return response.json()
        except httpx.TimeoutException as exc:
            last_error = exc
            log.warning(
                "StatPitch %s timed out (attempt %d/2) — likely a cold start.", path, attempt + 1
            )
        except httpx.HTTPStatusError as exc:
            # 4xx means we built a bad request; retrying sends the same one.
            if exc.response.status_code < 500:
                raise StatPitchError(
                    f"StatPitch {path} returned HTTP {exc.response.status_code}: "
                    f"{exc.response.text[:200]}"
                ) from exc
            last_error = exc
        except httpx.RequestError as exc:
            last_error = exc
            log.warning("StatPitch %s unreachable (attempt %d/2): %s", path, attempt + 1, exc)

        if attempt == 0:
            await asyncio.sleep(2)

    raise StatPitchError(f"StatPitch {path} failed after 2 attempts: {last_error}")


async def fetch_health(client: httpx.AsyncClient) -> SPHealth:
    """Poll before a sync batch.

    A failed artifact load comes back as `ready: false` with an `error` field
    rather than a 500, which is what lets a caller tell "still booting" from
    "broken" and back off accordingly.
    """
    return SPHealth.model_validate(await _get_json(client, "/health"))


async def fetch_fixture_window(
    client: httpx.AsyncClient,
    start: date,
    end: date,
    competitions: set[str] | None = None,
) -> FixtureWindow:
    """Fetch every fixture in [start, end] with predictions attached.

    One paged batch call, not one call per fixture. `from` is always sent
    explicitly: it defaults to today upstream and past fixtures are excluded
    unless asked for, so yesterday would silently vanish without it.

    Competitions are filtered here rather than upstream — the endpoint takes a
    single `competition_id`, so filtering server-side would mean one cold-start
    round trip per league instead of one for all of them.
    """
    result = FixtureWindow()
    offset = 0

    for _ in range(_MAX_PAGES):
        payload = await _get_json(
            client,
            "/fixtures/upcoming",
            params={
                "from": start.isoformat(),
                "to": end.isoformat(),
                "include_predictions": "true",
                "limit": _PAGE_SIZE,
                "offset": offset,
            },
        )
        page = SPFixturesPage.model_validate(payload)

        if page.refusal is not None and not page.refusal.available:
            raise StatPitchRefusal(page.refusal.reason_code, page.refusal.reason)

        result.model_version = page.model_version or result.model_version
        result.config_version = page.config_version or result.config_version
        result.generated_at_source = page.generated_at_source or result.generated_at_source
        result.fixtures.extend(page.fixtures)

        offset += page.count or len(page.fixtures)
        if page.count == 0 or offset >= page.total:
            break
    else:
        result.warnings.append(
            f"Stopped paging /fixtures/upcoming after {_MAX_PAGES} pages; "
            "some fixtures may be missing."
        )

    if competitions:
        kept = [f for f in result.fixtures if f.competition_id in competitions]
        dropped = len(result.fixtures) - len(kept)
        if dropped:
            log.info("Filtered out %d fixture(s) outside the configured competitions.", dropped)
        result.fixtures = kept

    missing_predictions = [f.fixture_id for f in result.fixtures if f.prediction is None]
    if missing_predictions:
        result.warnings.append(
            f"{len(missing_predictions)} fixture(s) came back without a prediction and "
            "were skipped."
        )

    fallback = [f for f in result.fixtures if f.prediction_source == "elo-poisson"]
    if fallback:
        # Measurably weaker than the fitted model (+0.0064 log-loss). Usually a
        # newly added fixture that missed the last precompute run.
        result.warnings.append(
            f"{len(fallback)} fixture(s) used the elo-poisson fallback rather than the "
            "fitted goal model."
        )

    return result
