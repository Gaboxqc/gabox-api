"""The StatPitch HTTP surface."""

from datetime import timedelta

import pytest

from api.statpitch.client import StatPitchError, StatPitchRefusal
from api.statpitch.clock import today_local


class TestWindow:
    def test_reports_three_local_dates(self, client):
        body = client.get("/statpitch/fixtures/window").json()
        today = today_local()

        assert body["today"] == today.isoformat()
        assert body["yesterday"] == (today - timedelta(days=1)).isoformat()
        assert body["tomorrow"] == (today + timedelta(days=1)).isoformat()


class TestFixtureLists:
    def test_empty_day_returns_an_empty_list_not_404(self, client):
        """An empty day is a normal answer.

        Roughly 88% of fixtures sit on a matchday placeholder upstream, so a
        real matchday can legitimately show nothing under today's date.
        """
        response = client.get("/statpitch/fixtures/today")
        assert response.status_code == 200
        assert response.json() == []

    def test_lists_the_three_day_window(self, client, make_fixture, seed_fixtures):
        today = today_local()
        seed_fixtures(
            make_fixture(match_date=today - timedelta(days=1), source_date=today),
            make_fixture(match_date=today, source_date=today),
            make_fixture(match_date=today + timedelta(days=1), source_date=today),
        )

        response = client.get("/statpitch/fixtures")
        assert response.status_code == 200
        assert len(response.json()) == 3
        assert response.headers["X-Total-Count"] == "3"

    def test_excludes_anything_outside_the_window(self, client, make_fixture, seed_fixtures):
        old = today_local() - timedelta(days=5)
        seed_fixtures(make_fixture(match_date=old, source_date=old))

        assert client.get("/statpitch/fixtures").json() == []

    @pytest.mark.parametrize("day", ["yesterday", "today", "tomorrow"])
    def test_each_day_endpoint_returns_only_that_day(
        self, client, make_fixture, seed_fixtures, day
    ):
        today = today_local()
        offsets = {"yesterday": -1, "today": 0, "tomorrow": 1}
        for name, offset in offsets.items():
            match_date = today + timedelta(days=offset)
            seed_fixtures(
                make_fixture(
                    match_date=match_date, source_date=match_date, home_team=f"{name} home"
                )
            )

        body = client.get(f"/statpitch/fixtures/{day}").json()
        assert len(body) == 1
        assert body[0]["home_team"] == f"{day} home"

    def test_filters_by_competition(self, client, make_fixture, seed_fixtures):
        seed_fixtures(
            make_fixture(competition_id="ESP.LALIGA"),
            make_fixture(competition_id="ENG.PL"),
        )

        body = client.get("/statpitch/fixtures", params={"competition_id": "ENG.PL"}).json()
        assert len(body) == 1
        assert body[0]["competition_id"] == "ENG.PL"

    def test_value_bets_only_filter(self, client, make_fixture, seed_fixtures):
        seed_fixtures(
            make_fixture(best_overall_bet="home_win", best_overall_kelly=0.05),
            make_fixture(best_overall_bet=None),
        )

        body = client.get("/statpitch/fixtures", params={"value_bets_only": True}).json()
        assert len(body) == 1

    def test_exposes_date_confirmed_for_provisional_dates(
        self, client, make_fixture, seed_fixtures
    ):
        seed_fixtures(make_fixture(date_confirmed=False, kickoff=None))
        body = client.get("/statpitch/fixtures/today").json()

        # The frontend needs this to render "date TBC" instead of a wrong day.
        assert body[0]["date_confirmed"] is False
        assert body[0]["kickoff"] is None


class TestTodayHighlights:
    def test_best_picks_the_most_confident_fixture(self, client, make_fixture, seed_fixtures):
        seed_fixtures(
            make_fixture(home_win_prob=0.40, draw_prob=0.30, away_win_prob=0.30),
            make_fixture(
                home_win_prob=0.80,
                draw_prob=0.12,
                away_win_prob=0.08,
                home_team="Strong favourite",
            ),
        )

        body = client.get("/statpitch/fixtures/today/best").json()
        assert body["home_team"] == "Strong favourite"

    def test_best_returns_404_when_nothing_is_cached(self, client):
        assert client.get("/statpitch/fixtures/today/best").status_code == 404

    def test_value_bets_are_ranked_by_kelly(self, client, make_fixture, seed_fixtures):
        seed_fixtures(
            make_fixture(
                best_overall_bet="home_win", best_overall_kelly=0.03, home_team="Smaller edge"
            ),
            make_fixture(
                best_overall_bet="over_2_5", best_overall_kelly=0.09, home_team="Bigger edge"
            ),
            make_fixture(best_overall_bet=None, home_team="No edge"),
        )

        body = client.get("/statpitch/fixtures/today/value-bets").json()
        assert [f["home_team"] for f in body] == ["Bigger edge", "Smaller edge"]


class TestFixtureDetail:
    def test_fetches_by_primary_key(self, client, make_fixture, seed_fixtures):
        (fixture,) = seed_fixtures(make_fixture(home_team="Detail"))
        body = client.get(f"/statpitch/fixtures/{fixture.id}").json()
        assert body["home_team"] == "Detail"

    def test_unknown_id_is_404(self, client):
        assert client.get("/statpitch/fixtures/9999").status_code == 404


class TestStats:
    def test_reports_the_window_and_both_series(self, client):
        body = client.get("/statpitch/stats").json()

        assert body["timezone"] == "America/Managua"
        assert body["generated_for"] == today_local().isoformat()
        assert [entry["basis"] for entry in body["roi"]] == ["1x2", "overall"]
        # Nothing settled, so no ROI can be claimed.
        assert all(entry["week"]["roi_pct"] is None for entry in body["roi"])

    def test_counts_today(self, client, make_fixture, seed_fixtures):
        seed_fixtures(
            make_fixture(home_win_prob=0.80, draw_prob=0.12, away_win_prob=0.08),
            make_fixture(best_overall_bet="home_win", best_overall_kelly=0.05),
            make_fixture(
                match_date=today_local() + timedelta(days=1),
                source_date=today_local() + timedelta(days=1),
            ),
        )

        body = client.get("/statpitch/stats").json()
        assert body["fixtures_today"] == 2
        assert body["fixtures_tomorrow"] == 1
        assert body["high_confidence_today"] == 1
        assert body["value_bets_today"] == 1


class TestLedger:
    def test_starts_empty(self, client):
        response = client.get("/statpitch/ledger")
        assert response.status_code == 200
        assert response.json() == []
        assert response.headers["X-Total-Count"] == "0"

    def test_rejects_an_unknown_basis(self, client):
        response = client.get("/statpitch/ledger", params={"basis": "parlay"})
        assert response.status_code == 422
        assert "1x2" in response.json()["detail"]


class TestSyncAuth:
    def test_requires_the_api_key(self, client):
        assert client.post("/statpitch/sync").status_code in (401, 403)

    def test_upstream_refusal_becomes_a_502(self, client, auth, monkeypatch):
        """A refusal is a 200 upstream, but it is not a success for us."""

        async def _refuse(_db):
            raise StatPitchRefusal("NO_FIXTURE_SOURCE", "the fixture artifact is not loaded")

        monkeypatch.setattr("api.statpitch.routers.fixtures.run_sync", _refuse)

        response = client.post("/statpitch/sync", headers=auth)
        assert response.status_code == 502
        assert "NO_FIXTURE_SOURCE" in response.json()["detail"]

    def test_upstream_outage_becomes_a_502(self, client, auth, monkeypatch):
        async def _fail(_db):
            raise StatPitchError("StatPitch /health failed after 2 attempts")

        monkeypatch.setattr("api.statpitch.routers.fixtures.run_sync", _fail)

        assert client.post("/statpitch/sync", headers=auth).status_code == 502
