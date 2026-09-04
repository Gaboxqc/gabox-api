"""The competition registry.

`competition_id` used to be a bare string with nothing behind it — the API
returned "ENG.PL" and no readable name at all. These tests are about that string
now meaning something, and meaning it in one place.
"""

from sqlmodel import Session, select

from api.statpitch.competitions import StatPitchCompetition, all_competitions, by_id, seed
from api.statpitch.leagues import ALL_COMPETITIONS, FREE_TIER_LEAGUES

# ── Seeding ──────────────────────────────────────────────────────────────────


def test_all_twelve_are_seeded(engine):
    with Session(engine) as db:
        assert seed(db) == 15
        assert {row.competition_id for row in all_competitions(db)} == ALL_COMPETITIONS


def test_seeding_twice_adds_nothing(engine):
    """Safe to call on every boot, which is the point."""
    with Session(engine) as db:
        seed(db)
        assert seed(db) == 0
        assert len(all_competitions(db)) == 15


def test_seeding_leaves_an_existing_icon_alone(engine):
    """A re-seed must not undo a backfill."""
    with Session(engine) as db:
        seed(db)
        row = by_id(db, "ENG.PL")
        row.icon_url = "https://assets.example.com/statpitch/competitions/eng-pl/abc-512.webp"
        db.add(row)
        db.commit()

        seed(db)
        assert by_id(db, "ENG.PL").icon_url is not None


def test_every_competition_has_a_name_and_a_short_name(engine):
    with Session(engine) as db:
        seed(db)
        for row in all_competitions(db):
            assert row.name
            assert row.short_name
            assert row.espn_slug


def test_the_short_name_is_the_one_that_fits_a_chip(engine):
    with Session(engine) as db:
        seed(db)
        assert by_id(db, "ENG.PL").name == "English Premier League"
        assert by_id(db, "ENG.PL").short_name == "Premier League"


def test_an_unknown_competition_is_not_invented(engine):
    with Session(engine) as db:
        seed(db)
        assert by_id(db, "MARS.SUPERLEAGUE") is None


# ── The endpoint ─────────────────────────────────────────────────────────────


def test_the_endpoint_lists_every_competition(client, engine):
    with Session(engine) as db:
        seed(db)

    body = client.get("/statpitch/competitions").json()
    assert len(body) == 15
    assert {row["competition_id"] for row in body} == ALL_COMPETITIONS


def test_the_endpoint_is_ungated(client, engine):
    """Which competitions exist is navigation, not product — an anonymous
    visitor needs it to render a filter bar at all."""
    with Session(engine) as db:
        seed(db)

    assert client.get("/statpitch/competitions").status_code == 200


def test_the_endpoint_says_which_are_free(client, engine):
    """So the seven cups can be shown as an upgrade rather than discovered by
    getting an empty list back."""
    with Session(engine) as db:
        seed(db)

    body = client.get("/statpitch/competitions").json()
    free = {row["competition_id"] for row in body if row["free_tier"]}

    assert free == FREE_TIER_LEAGUES
    assert len(free) == 5
    # Ten of fifteen are an upgrade, not an empty list.
    assert len(body) - len(free) == 10


def test_an_unseeded_registry_returns_an_empty_list(client):
    """Not a 500. An empty registry is a deploy that has not seeded yet."""
    response = client.get("/statpitch/competitions")
    assert response.status_code == 200
    assert response.json() == []


# ── On a fixture ─────────────────────────────────────────────────────────────


def test_a_fixture_reports_its_competition_name(client, auth, make_fixture, seed_fixtures):
    """The frontend used to get "ENG.PL" and nothing else."""
    seed_fixtures(make_fixture(competition_id="ENG.PL"))

    (body,) = client.get("/statpitch/fixtures/today", headers=auth).json()
    assert body["competition_id"] == "ENG.PL"
    assert body["competition_name"] == "English Premier League"
    assert body["competition_short_name"] == "Premier League"


def test_the_name_reaches_the_free_shape_too(client, make_fixture, seed_fixtures):
    """A competition heading is not something anybody is paying for."""
    seed_fixtures(make_fixture(competition_id="ENG.PL"))

    (body,) = client.get("/statpitch/fixtures/today").json()
    assert body["competition_name"] == "English Premier League"


def test_an_icon_reaches_a_cached_fixture_immediately(
    client, auth, engine, make_fixture, seed_fixtures
):
    """Read through the reference, so an icon resolved after a fixture was
    cached needs no sync to appear — the same property the crests have."""
    seed_fixtures(make_fixture(competition_id="ENG.PL"))

    (before,) = client.get("/statpitch/fixtures/today", headers=auth).json()
    assert before["competition_icon_url"] is None

    icon = "https://assets.example.com/statpitch/competitions/eng-pl/abc-512.webp"
    with Session(engine) as db:
        row = by_id(db, "ENG.PL")
        row.icon_url = icon
        db.add(row)
        db.commit()

    # No re-sync, no second pass over the fixture cache.
    (after,) = client.get("/statpitch/fixtures/today", headers=auth).json()
    assert after["competition_icon_url"] == icon


def test_reading_a_fixture_list_costs_a_fixed_number_of_queries(
    engine, make_fixture, seed_fixtures
):
    """The clubs and the competition are joined; the selections are `selectin`.

    So a fixture list is two statements, not one — but two regardless of how
    many fixtures are in it, which is the property that actually matters. The
    count is asserted as *constant across page size* rather than as a literal,
    because a literal only ever catches the change and never the regression:
    an N+1 introduced here would still read as "one more query" at five rows.

    The selections deliberately are not joined. A joined collection beside two
    joined clubs and a joined competition multiplies every fixture row by its
    selection count, which is a worse trade than one extra statement.
    """
    from sqlalchemy import event

    from api.statpitch.models import StatPitchFixture

    def queries_for(n: int) -> tuple[int, int]:
        seed_fixtures(*[make_fixture() for _ in range(n)])
        count = {"n": 0}
        handler = lambda *a, **k: count.__setitem__("n", count["n"] + 1)  # noqa: E731
        event.listen(engine, "before_cursor_execute", handler)
        try:
            with Session(engine) as db:
                rows = db.exec(select(StatPitchFixture)).all()
                _ = [
                    (row.competition_name, row.home_team, row.away_team, row.selections)
                    for row in rows
                ]
        finally:
            event.remove(engine, "before_cursor_execute", handler)
        return len(rows), count["n"]

    five, cost_of_five = queries_for(5)
    twenty, cost_of_twenty = queries_for(15)

    assert (five, twenty) == (5, 20)
    assert cost_of_five == cost_of_twenty


def test_a_fixture_cannot_name_a_competition_that_does_not_exist(engine, make_fixture):
    """The foreign key is what stops "ENG.PLL" becoming a silent thirteenth
    competition nobody notices."""
    from sqlalchemy.exc import IntegrityError

    with Session(engine) as db:
        seed(db)
        row = db.exec(select(StatPitchCompetition)).first()
        assert row is not None

        fixture = make_fixture()
        fixture.competition_id = "ENG.PLL"
        db.add(fixture)
        try:
            db.commit()
        except IntegrityError:
            return
    raise AssertionError("an unknown competition_id was accepted")


# ── Reading a league badge off ESPN ───────────────────────────────────────────


def _scoreboard(*logos: dict) -> dict:
    return {"leagues": [{"id": "700", "name": "English Premier League", "logos": list(logos)}]}


def test_the_dark_league_badge_is_preferred():
    """The UI is near-black, so the dark variant is the one worth having."""
    from api.statpitch.crests import _pick_league_logo

    picked = _pick_league_logo(
        _scoreboard(
            {
                "href": "https://a.espncdn.com/i/leaguelogos/soccer/500/23.png",
                "rel": ["full", "default"],
            },
            {
                "href": "https://a.espncdn.com/i/leaguelogos/soccer/500-dark/23.png",
                "rel": ["full", "dark"],
            },
        )
    )
    assert picked.endswith("500-dark/23.png")


def test_a_competition_without_a_dark_badge_falls_back():
    """Coppa Italia publishes only the light one."""
    from api.statpitch.crests import _pick_league_logo

    picked = _pick_league_logo(
        _scoreboard(
            {"href": "https://a.espncdn.com/i/leaguelogos/soccer/500/2569.png", "rel": ["full"]}
        )
    )
    assert picked.endswith("500/2569.png")


def test_a_competition_with_no_badge_at_all_is_none():
    """A missing icon is a normal state, the same as a missing crest."""
    from api.statpitch.crests import _pick_league_logo

    assert _pick_league_logo(_scoreboard()) is None
    assert _pick_league_logo({}) is None


class TestTheThreeFlags:
    """`free_tier`, `priced` and `stakeable` answer three different questions.

    They named the same five leagues until StatPitch added three more it can
    price, at which point every pair of them came apart.
    """

    def test_a_cup_is_none_of_the_three(self, client, engine):
        with Session(engine) as db:
            seed(db)

        rows = {r["competition_id"]: r for r in client.get("/statpitch/competitions").json()}
        cup = rows["UEFA.UCL"]

        assert (cup["free_tier"], cup["priced"], cup["stakeable"]) == (False, False, False)

    def test_a_core_league_is_all_three(self, client, engine):
        with Session(engine) as db:
            seed(db)

        rows = {r["competition_id"]: r for r in client.get("/statpitch/competitions").json()}
        epl = rows["ENG.PL"]

        assert (epl["free_tier"], epl["priced"], epl["stakeable"]) == (True, True, True)

    def test_the_super_lig_is_priced_and_stakeable_but_not_free(self, client, engine):
        """The flags coming apart, case one: a paid league you can bet."""
        with Session(engine) as db:
            seed(db)

        rows = {r["competition_id"]: r for r in client.get("/statpitch/competitions").json()}
        tur = rows["TUR.SUPERLIG"]

        assert tur["priced"] and tur["stakeable"]
        assert not tur["free_tier"]

    def test_the_eredivisie_is_priced_but_never_stakeable(self, client, engine):
        """The flags coming apart, case two, and the one users will ask about.

        Served in full — fixtures, predictions, prices — and permanently outside
        the staking scope, because its own CLV estimate is negative. Without
        this flag the frontend has nothing to say but "no picks", which reads as
        a broken product rather than a measured result.
        """
        with Session(engine) as db:
            seed(db)

        rows = {r["competition_id"]: r for r in client.get("/statpitch/competitions").json()}

        for league in ("NED.EREDIVISIE", "POR.PRIMEIRA"):
            assert rows[league]["priced"], league
            assert not rows[league]["stakeable"], league

    def test_the_counts_match_the_four_sets(self, client, engine):
        with Session(engine) as db:
            seed(db)

        rows = client.get("/statpitch/competitions").json()

        assert len(rows) == 15
        assert sum(1 for r in rows if r["priced"]) == 8
        assert sum(1 for r in rows if r["stakeable"]) == 6
        assert sum(1 for r in rows if r["free_tier"]) == 5


class TestTheScopeComesFromWhatWasSynced:
    """`stakeable` is read from the last synced day, not from a constant.

    The scope is re-measured upstream and moves. A hardcoded answer would keep
    reporting last season's, and the failure would be silent.
    """

    @staticmethod
    def _bet_day(engine, scope):
        from api.statpitch.clock import today_local
        from api.statpitch.models import StatPitchBetDay

        with Session(engine) as db:
            db.add(StatPitchBetDay(match_date=today_local(), selection_rule_competitions=scope))
            db.commit()

    def test_an_unsynced_database_falls_back_to_the_constant(self, engine):
        from api.statpitch.bets import stakeable_competitions
        from api.statpitch.leagues import STAKEABLE_LEAGUES

        with Session(engine) as db:
            assert stakeable_competitions(db) == STAKEABLE_LEAGUES

    def test_a_synced_scope_wins_over_the_constant(self, engine, client):
        """The Primeira Liga is expected back on a later re-measurement. When
        that happens the API must follow upstream, not our constant."""
        from api.statpitch.bets import stakeable_competitions
        from api.statpitch.leagues import STAKEABLE_LEAGUES

        self._bet_day(engine, ["ENG.PL", "POR.PRIMEIRA"])

        with Session(engine) as db:
            seed(db)
            assert stakeable_competitions(db) == frozenset({"ENG.PL", "POR.PRIMEIRA"})
            assert stakeable_competitions(db) != STAKEABLE_LEAGUES

        rows = {r["competition_id"]: r for r in client.get("/statpitch/competitions").json()}
        assert rows["POR.PRIMEIRA"]["stakeable"]
        assert not rows["ITA.SERIEA"]["stakeable"]

    def test_an_empty_scope_is_ignored_rather_than_believed(self, engine):
        """A scope of nothing is not a scope. Believing one malformed sync would
        mark every competition unbettable across the whole product."""
        from api.statpitch.bets import stakeable_competitions
        from api.statpitch.leagues import STAKEABLE_LEAGUES

        self._bet_day(engine, [])

        with Session(engine) as db:
            assert stakeable_competitions(db) == STAKEABLE_LEAGUES
