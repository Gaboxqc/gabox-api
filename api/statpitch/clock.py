"""The single definition of "today".

Every date boundary in StatPitch is the frontend's local day, not the server's.
The API runs on UTC and Nicaragua is UTC-6, so between 18:00 and midnight local
the two disagree — which is exactly the window in which the old code wrote rows
under one date and read them under another.

Nothing outside this module may call `date.today()`.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from api.core.config import settings


def local_zone() -> ZoneInfo:
    return ZoneInfo(settings.statpitch_timezone)


def now_local() -> datetime:
    return datetime.now(local_zone())


def today_local() -> date:
    """The day the frontend is currently showing. Rolls at local midnight."""
    return now_local().date()


def to_local_date(moment: datetime) -> date:
    """Bucket a UTC instant into a local day.

    A naive datetime is read as UTC: every timestamp we ingest is documented as
    UTC, and guessing the server's zone instead would reintroduce the same
    off-by-one this module exists to prevent.
    """
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(local_zone()).date()


@dataclass(frozen=True)
class Window:
    """The three days the frontend shows, in local time."""

    yesterday: date
    today: date
    tomorrow: date

    @property
    def start(self) -> date:
        return self.yesterday

    @property
    def end(self) -> date:
        return self.tomorrow

    def contains(self, day: date) -> bool:
        return self.start <= day <= self.end


def current_window(span_days: int | None = None) -> Window:
    """Yesterday, today and tomorrow — the fixture cache's whole lifetime."""
    span = settings.statpitch_retention_days if span_days is None else span_days
    today = today_local()
    return Window(
        yesterday=today - timedelta(days=span),
        today=today,
        tomorrow=today + timedelta(days=span),
    )
