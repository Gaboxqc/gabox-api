"""Reading final scores from ESPN, and the one way this could go badly wrong.

`tests/fixtures/statpitch/espn_scoreboard.json` is a real capture from the
`eng.1` scoreboard, trimmed to the fields consumed but structurally untouched.
It deliberately holds three finished matches and one scheduled one, because the
difference between them is the sharpest edge in this module.
"""

import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from api.statpitch.scores_service import (
    _build_score,
    _date_range,
    _parse_score,
    fetch_scores,
)
from api.statpitch.settlement import apply_scores

FIXTURES = Path(__file__).parent / "fixtures" / "statpitch"


def _events() -> list[dict]:
    payload = json.loads((FIXTURES / "espn_scoreboard.json").read_text(encoding="utf-8"))
    return payload["events"]


def _mock_espn(monkeypatch, handler) -> None:
    """Point the scores service at a mock transport.

    The real class is captured *before* patching. `scores_service.httpx` is the
    httpx module itself, so setting the attribute patches httpx globally — and a
    factory that reached for `httpx.AsyncClient` would then be calling itself.
    """
    real = httpx.AsyncClient
    monkeypatch.setattr(
        "api.statpitch.scores_service.httpx.AsyncClient",
        lambda **kwargs: real(transport=httpx.MockTransport(handler)),
    )


class TestParsing:
    def test_a_finished_match_yields_goal_counts(self):
        score = _build_score(_events()[0], "ENG.PL")

        assert score.home_team == "Crystal Palace"
        assert score.away_team == "Manchester City"
        assert (score.home_score, score.away_score) == (1, 4)
        assert score.completed
        assert score.actual_result == "away_win"

    def test_a_draw_reads_as_a_draw(self):
        score = _build_score(_events()[1], "ENG.PL")

        assert (score.home_score, score.away_score) == (2, 2)
        assert score.actual_result == "draw"

    def test_scores_arrive_as_strings_and_become_integers(self):
        """ESPN sends `"score": "4"`, not `4`. Settling needs the number."""
        assert _parse_score("4") == 4
        assert _parse_score(0) == 0
        assert _parse_score(None) is None
        assert _parse_score("") is None
        assert _parse_score("abandoned") is None

    def test_the_kickoff_is_an_aware_utc_instant(self):
        """`apply_scores` compares this against a local match day to pick the
        right leg of a double fixture, so a naive value would land it wrong."""
        score = _build_score(_events()[0], "ENG.PL")

        assert score.commence_time.tzinfo is not None
        assert score.commence_time.utcoffset() == datetime.now(UTC).utcoffset()

    def test_a_malformed_event_is_dropped_rather_than_guessed(self):
        assert _build_score({}, "ENG.PL") is None
        assert _build_score({"date": "2026-08-30T13:00Z", "competitions": []}, "ENG.PL") is None
        assert _build_score({"competitions": [{"competitors": []}]}, "ENG.PL") is None


class TestAScheduledMatchIsNotADraw:
    """The sharpest edge in the module.

    ESPN reports a match that has not kicked off as `"score": "0"` for both
    sides — not null. Read on goal counts alone, every unplayed fixture in the
    window looks like a completed 0-0 and would settle as a draw: silently,
    plausibly, and across the whole card. `completed` is the only thing that
    separates them, and it is what everything downstream filters on.
    """

    def test_a_scheduled_match_reports_zero_zero(self):
        scheduled = _build_score(_events()[3], "ENG.PL")

        assert (scheduled.home_score, scheduled.away_score) == (0, 0)
        assert not scheduled.completed

    def test_it_has_no_result(self):
        scheduled = _build_score(_events()[3], "ENG.PL")

        assert scheduled.actual_result is None

    def test_settlement_refuses_it(self, engine, make_fixture, seed_fixtures):
        """The guard that matters. If this ever fails, unplayed fixtures are
        being banked as draws."""
        from sqlmodel import Session

        fixture = seed_fixtures(make_fixture(home_team="AFC Bournemouth", away_team="Brentford"))[0]
        scheduled = _build_score(_events()[3], "ENG.PL")

        with Session(engine) as db:
            settled = apply_scores(db, [db.get(type(fixture), fixture.id)], [scheduled])

        assert settled == 0


class TestDateRange:
    def test_the_range_spans_the_lookback_through_tomorrow(self):
        """One request per competition instead of one per day."""
        start, _, end = _date_range(3).partition("-")

        assert len(start) == 8 and len(end) == 8
        assert start.isdigit() and end.isdigit()
        assert int(end) > int(start)

    def test_a_zero_lookback_still_covers_a_day(self):
        assert _date_range(0).count("-") == 1


class TestFetching:
    @pytest.mark.anyio
    async def test_a_failing_competition_warns_rather_than_raises(self, monkeypatch):
        """The behaviour change from the Odds API era.

        There is no key and no quota, so there is no configuration failure worth
        aborting a sync for. A run that priced fixtures but settled nothing has
        still done most of its job, and the next run picks the results up.
        """

        def handler(request: httpx.Request) -> httpx.Response:
            if "eng.1" in str(request.url):
                return httpx.Response(500, text="upstream boom")
            return httpx.Response(200, json={"events": _events()})

        _mock_espn(monkeypatch, handler)

        result = await fetch_scores({"ENG.PL", "ESP.LALIGA"})

        assert any("ENG.PL" in w for w in result.warnings)
        assert result.scores, "the healthy competition should still have settled"

    @pytest.mark.anyio
    async def test_a_competition_with_no_espn_slug_is_reported(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"events": []})

        _mock_espn(monkeypatch, handler)

        result = await fetch_scores({"NOT.A.LEAGUE"})

        assert result.scores == []
        assert any("NOT.A.LEAGUE" in w and "cannot settle" in w for w in result.warnings)

    @pytest.mark.anyio
    async def test_scores_are_returned_with_their_competition(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"events": _events()})

        _mock_espn(monkeypatch, handler)

        result = await fetch_scores({"ENG.PL"})

        assert result.requests_used == 1
        assert {s.competition_id for s in result.scores} == {"ENG.PL"}
        assert sum(1 for s in result.scores if s.completed) == 3
