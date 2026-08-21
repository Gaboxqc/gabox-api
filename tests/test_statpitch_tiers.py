"""What each tier actually receives.

Two kinds of assertion here, and both matter:

- the **policy** table says the right thing, and fails closed when asked
  something it does not recognise;
- the **wire** matches it — a free caller's JSON has no odds in it, whatever the
  policy claims.

The second is the one that would catch a regression. A gated field arriving as
`null` rather than being absent still counts as a leak of the shape, and a
`response_model` that quietly truncates a paid response is a bug nothing else
would notice.
"""

from datetime import timedelta

import pytest
from sqlmodel import Session, select

from api.core.config import settings
from api.statpitch.accounts.models import StatPitchAccount, utcnow
from api.statpitch.leagues import ALL_COMPETITIONS, STATPITCH_ODDS_COVERAGE
from api.statpitch.serialization import FixtureFreeRead, FixtureFullRead, serialize_fixture
from api.statpitch.tiers import (
    POLICIES,
    Feature,
    allows,
    is_at_least,
    policy_for,
    visible_competitions,
)

EMAIL = "bettor@example.com"
PASSWORD = "correct-horse-battery-staple"

# Everything a free caller must never receive.
GATED_FIELDS = (
    "odds_home",
    "odds_over_2_5",
    "ev_home",
    "kelly_home",
    "best_bet",
    "best_overall_bet",
    "over_2_5",
    "btts_yes",
    "correct_scores",
    "explanation",
    "home_elo",
    "home_xg",
    "fully_rated",
)


@pytest.fixture(name="as_tier")
def as_tier_fixture(client, engine):
    """Sign in an account on a given tier and leave its cookie in the jar."""

    def _as_tier(tier: str, expires_at=None):
        client.cookies.clear()
        response = client.post(
            "/statpitch/accounts/register", json={"email": EMAIL, "password": PASSWORD}
        )
        assert response.status_code == 201, response.text

        with Session(engine) as db:
            account = db.exec(select(StatPitchAccount)).first()
            account.tier = tier
            account.tier_expires_at = expires_at
            db.add(account)
            db.commit()

        return {"X-CSRF-Token": response.json()["csrf_token"]}

    return _as_tier


# ── The policy table ─────────────────────────────────────────────────────────


def test_free_is_capped_at_three_predictions_a_day():
    assert policy_for("free").daily_predictions == 3


def test_paid_tiers_are_uncapped():
    assert policy_for("pro").daily_predictions is None
    assert policy_for("elite").daily_predictions is None


def test_free_sees_only_the_priced_leagues():
    """Five, and they are the same five we can price — one idea, one constant."""
    assert visible_competitions("free") == STATPITCH_ODDS_COVERAGE
    assert len(visible_competitions("free")) == 5


def test_paid_tiers_see_all_twelve():
    assert visible_competitions("pro") == ALL_COMPETITIONS
    assert visible_competitions("elite") == ALL_COMPETITIONS
    assert len(ALL_COMPETITIONS) == 12


@pytest.mark.parametrize(
    "feature",
    [
        Feature.MARKET_BREAKDOWN,
        Feature.EDGE_INDICATORS,
        Feature.CONFIDENCE,
        Feature.LEDGER_ROI,
        Feature.API_ACCESS,
    ],
)
def test_free_has_no_paid_feature(feature):
    assert allows("free", feature) is False


@pytest.mark.parametrize(
    "feature",
    [
        Feature.MARKET_BREAKDOWN,
        Feature.EDGE_INDICATORS,
        Feature.CONFIDENCE,
        Feature.LEDGER_ROI,
    ],
)
def test_pro_has_everything_but_the_api(feature):
    assert allows("pro", feature) is True


def test_api_access_is_what_elite_adds():
    """The pricing page gives Elite nothing else over Pro."""
    assert allows("pro", Feature.API_ACCESS) is False
    assert allows("elite", Feature.API_ACCESS) is True
    assert POLICIES["elite"].features - POLICIES["pro"].features == {Feature.API_ACCESS}


def test_an_unrecognised_tier_fails_closed():
    """A typo in a manual grant, or a value written by a newer deploy, must not
    hand out Elite."""
    assert policy_for("platinum") is POLICIES["free"]
    assert allows("platinum", Feature.LEDGER_ROI) is False


def test_tier_ordering_is_weakest_first():
    assert is_at_least("elite", "pro") is True
    assert is_at_least("free", "pro") is False
    assert is_at_least("pro", "pro") is True


# ── Serialisation ────────────────────────────────────────────────────────────


@pytest.fixture(name="saved")
def saved_fixture(make_fixture, seed_fixtures):
    """A persisted fixture. The read shapes require a real primary key, so an
    unsaved row would fail validation before any gating was exercised."""

    def _saved(**overrides):
        (row,) = seed_fixtures(make_fixture(**overrides))
        return row

    return _saved


def test_the_free_shape_omits_every_gated_field(saved):
    payload = serialize_fixture(saved(), "free").model_dump()

    for field in GATED_FIELDS:
        assert field not in payload, f"{field} leaked into the free shape"


def test_gated_fields_are_absent_rather_than_null(saved):
    """`null` is indistinguishable from "no market offered", and a frontend
    would render it as missing data rather than as an upsell."""
    payload = serialize_fixture(saved(), "free").model_dump()
    assert "odds_home" not in payload


def test_the_free_shape_still_carries_what_free_is_sold(saved):
    payload = serialize_fixture(saved(home_team="Arsenal"), "free").model_dump()

    assert payload["home_team"] == "Arsenal"
    assert payload["home_win_prob"] is not None
    assert payload["draw_prob"] is not None
    assert payload["away_win_prob"] is not None
    # Crests are a free-tier visual, so they must survive the trim.
    assert "home_crest_url" in payload
    assert payload["locked"] is True


@pytest.mark.parametrize("tier", ["pro", "elite"])
def test_paid_tiers_get_the_full_shape(tier, saved):
    payload = serialize_fixture(saved(), tier).model_dump()

    for field in GATED_FIELDS:
        assert field in payload
    assert payload["locked"] is False


def test_pro_and_elite_receive_identical_shapes(saved):
    """Elite buys API access, not more data. If that ever stops being true it
    should be a deliberate edit here."""
    fixture = saved()
    assert set(serialize_fixture(fixture, "pro").model_dump()) == set(
        serialize_fixture(fixture, "elite").model_dump()
    )


def test_the_shape_classes_are_what_they_claim(saved):
    row = saved()
    assert isinstance(serialize_fixture(row, "free"), FixtureFreeRead)
    assert isinstance(serialize_fixture(row, "pro"), FixtureFullRead)


# ── Over the wire ────────────────────────────────────────────────────────────


def test_an_anonymous_caller_reads_as_free(client, make_fixture, seed_fixtures):
    seed_fixtures(make_fixture())
    (body,) = client.get("/statpitch/fixtures/today").json()

    assert body["locked"] is True
    assert "odds_home" not in body


def test_a_pro_caller_gets_the_numbers(client, as_tier, make_fixture, seed_fixtures):
    """The regression this exists for: `response_model` filters a response down
    to the declared model, so a single declared shape would silently truncate
    every paying caller."""
    seed_fixtures(make_fixture())
    as_tier("pro")

    (body,) = client.get("/statpitch/fixtures/today").json()
    assert body["locked"] is False
    assert "odds_home" in body
    assert "kelly_home" in body


def test_the_master_key_reads_as_elite(client, auth, make_fixture, seed_fixtures):
    """It has no account, but it is the owner's key — denying it the ledger
    would lock Gabriel out of his own dashboard."""
    seed_fixtures(make_fixture())
    (body,) = client.get("/statpitch/fixtures/today", headers=auth).json()
    assert body["locked"] is False


def test_an_expired_pro_reads_as_free_on_the_wire(client, as_tier, make_fixture, seed_fixtures):
    """The expiry has to reach the response, not just `/accounts/me`."""
    seed_fixtures(make_fixture())
    as_tier("pro", expires_at=utcnow() - timedelta(seconds=1))

    (body,) = client.get("/statpitch/fixtures/today").json()
    assert body["locked"] is True
    assert "odds_home" not in body


# ── Competition scope ────────────────────────────────────────────────────────


def test_free_does_not_see_a_cup_fixture(client, make_fixture, seed_fixtures):
    seed_fixtures(
        make_fixture(competition_id="ENG.PL", home_team="League"),
        make_fixture(competition_id="UEFA.UCL", home_team="Cup"),
    )

    body = client.get("/statpitch/fixtures/today").json()
    assert [row["home_team"] for row in body] == ["League"]


def test_pro_sees_both(client, as_tier, make_fixture, seed_fixtures):
    seed_fixtures(
        make_fixture(competition_id="ENG.PL", home_team="League"),
        make_fixture(competition_id="UEFA.UCL", home_team="Cup"),
    )
    as_tier("pro")

    body = client.get("/statpitch/fixtures/today").json()
    assert {row["home_team"] for row in body} == {"League", "Cup"}


def test_the_total_count_matches_what_was_actually_sent(client, make_fixture, seed_fixtures):
    """Counting rows the caller cannot see and then withholding them makes
    pagination lie."""
    seed_fixtures(
        make_fixture(competition_id="ENG.PL"),
        make_fixture(competition_id="UEFA.UCL"),
    )

    response = client.get("/statpitch/fixtures")
    assert response.headers["X-Total-Count"] == "1"
    assert len(response.json()) == 1


def test_a_hidden_fixture_is_reported_as_absent_not_forbidden(client, make_fixture, seed_fixtures):
    """403 would confirm it exists, and which fixtures exist in the other seven
    competitions is part of what Pro is buying."""
    (cup,) = seed_fixtures(make_fixture(competition_id="UEFA.UCL"))
    assert client.get(f"/statpitch/fixtures/{cup.id}").status_code == 404


def test_the_same_fixture_is_visible_to_pro(client, as_tier, make_fixture, seed_fixtures):
    (cup,) = seed_fixtures(make_fixture(competition_id="UEFA.UCL"))
    as_tier("pro")
    assert client.get(f"/statpitch/fixtures/{cup.id}").status_code == 200


# ── Whole-endpoint gates ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "path",
    ["/statpitch/stats", "/statpitch/ledger", "/statpitch/fixtures/today/value-bets"],
)
def test_the_paid_endpoints_refuse_a_free_caller(path, client):
    response = client.get(path)
    assert response.status_code == 402
    assert "Pro" in response.json()["detail"]


@pytest.mark.parametrize(
    "path",
    ["/statpitch/stats", "/statpitch/ledger", "/statpitch/fixtures/today/value-bets"],
)
def test_the_paid_endpoints_admit_a_pro_caller(path, client, as_tier):
    as_tier("pro")
    assert client.get(path).status_code == 200


def test_the_match_of_the_day_is_free(client, make_fixture, seed_fixtures):
    """It is on the free column of the pricing page — at free depth."""
    seed_fixtures(make_fixture())

    response = client.get("/statpitch/fixtures/today/best")
    assert response.status_code == 200
    assert response.json()["locked"] is True


def test_the_settings_still_expose_the_free_league_set():
    """The free scope is defined as "the leagues we can price", so a change to
    the sync's coverage moves the free tier with it — deliberately."""
    assert set(settings.statpitch_competitions) <= set(STATPITCH_ODDS_COVERAGE)
