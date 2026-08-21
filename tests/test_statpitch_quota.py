"""The free tier's three predictions a day.

The behaviour worth guarding is mostly about what does *not* cost an unlock:
browsing the list, refreshing a page, coming back tomorrow, being on a paid
tier. A quota that charges for any of those reads as a bug to the person paying
for it.
"""

from datetime import timedelta

import pytest
from sqlmodel import Session, select

from api.statpitch.accounts.models import StatPitchAccount, utcnow
from api.statpitch.clock import today_local
from api.statpitch.quota import (
    StatPitchPredictionUnlock,
    is_unlocked,
    remaining,
    spent_today,
    unlock,
    unlocked_ids,
)

EMAIL = "bettor@example.com"
PASSWORD = "correct-horse-battery-staple"
QUOTA_HEADER = "X-Predictions-Remaining"


@pytest.fixture(name="account")
def account_fixture(engine):
    with Session(engine) as db:
        account = StatPitchAccount(email=EMAIL, password_hash="x")
        db.add(account)
        db.commit()
        db.refresh(account)
        return account.id


@pytest.fixture(name="signup")
def signup_fixture(client, engine):
    """Register through the API and optionally move the account to a tier."""

    def _signup(tier: str = "free"):
        client.cookies.clear()
        response = client.post(
            "/statpitch/accounts/register", json={"email": EMAIL, "password": PASSWORD}
        )
        assert response.status_code == 201, response.text

        if tier != "free":
            with Session(engine) as db:
                account = db.exec(select(StatPitchAccount)).first()
                account.tier = tier
                db.add(account)
                db.commit()

        return {"X-CSRF-Token": response.json()["csrf_token"]}

    return _signup


# ── Counting ─────────────────────────────────────────────────────────────────


def test_a_new_account_has_its_full_allowance(engine, account):
    with Session(engine) as db:
        assert remaining(db, db.get(StatPitchAccount, account), "free", today_local()) == 3


def test_an_anonymous_caller_has_an_allowance_of_nothing(engine):
    """Zero, not unlimited. There is no honest way to count three a day against
    somebody with no account."""
    with Session(engine) as db:
        assert remaining(db, None, "free", today_local()) == 0


def test_a_paid_account_is_uncapped(engine, account):
    with Session(engine) as db:
        row = db.get(StatPitchAccount, account)
        row.tier = "pro"
        assert remaining(db, row, "pro", today_local()) is None


def test_each_unlock_costs_one(engine, account):
    with Session(engine) as db:
        row = db.get(StatPitchAccount, account)
        day = today_local()

        assert unlock(db, row, "free", "fixture-a", day) is True
        assert remaining(db, row, "free", day) == 2

        assert unlock(db, row, "free", "fixture-b", day) is True
        assert remaining(db, row, "free", day) == 1


def test_the_fourth_fixture_is_refused(engine, account):
    with Session(engine) as db:
        row = db.get(StatPitchAccount, account)
        day = today_local()

        for index in range(3):
            assert unlock(db, row, "free", f"fixture-{index}", day) is True

        assert remaining(db, row, "free", day) == 0
        assert unlock(db, row, "free", "fixture-4", day) is False


def test_remaining_never_goes_negative(engine, account):
    with Session(engine) as db:
        row = db.get(StatPitchAccount, account)
        day = today_local()
        for index in range(5):
            unlock(db, row, "free", f"fixture-{index}", day)
        assert remaining(db, row, "free", day) == 0


# ── What must not cost anything ──────────────────────────────────────────────


def test_reopening_the_same_fixture_is_free(engine, account):
    """Otherwise refreshing a page costs a reader their allowance."""
    with Session(engine) as db:
        row = db.get(StatPitchAccount, account)
        day = today_local()

        unlock(db, row, "free", "fixture-a", day)
        for _ in range(5):
            assert unlock(db, row, "free", "fixture-a", day) is True

        assert remaining(db, row, "free", day) == 2
        assert spent_today(db, row, day) == 1


def test_an_unlock_survives_into_the_next_day(engine, account):
    """The allowance resets; what was already revealed stays revealed."""
    with Session(engine) as db:
        row = db.get(StatPitchAccount, account)
        yesterday = today_local() - timedelta(days=1)

        unlock(db, row, "free", "fixture-a", yesterday)

        assert remaining(db, row, "free", today_local()) == 3
        assert is_unlocked(db, row, "fixture-a") is True
        assert unlock(db, row, "free", "fixture-a", today_local()) is True
        # Still free today, so the fresh allowance is untouched.
        assert remaining(db, row, "free", today_local()) == 3


def test_yesterdays_spending_does_not_count_against_today(engine, account):
    with Session(engine) as db:
        row = db.get(StatPitchAccount, account)
        yesterday = today_local() - timedelta(days=1)
        for index in range(3):
            unlock(db, row, "free", f"fixture-{index}", yesterday)

        assert remaining(db, row, "free", today_local()) == 3


def test_a_paid_account_records_nothing(engine, account):
    """A row per fixture view for every subscriber would grow a table nothing
    reads."""
    with Session(engine) as db:
        row = db.get(StatPitchAccount, account)
        row.tier = "pro"
        db.add(row)
        db.commit()

        assert unlock(db, row, "pro", "fixture-a", today_local()) is True
        assert db.exec(select(StatPitchPredictionUnlock)).all() == []


def test_an_expired_pro_is_rationed_again(engine, account):
    with Session(engine) as db:
        row = db.get(StatPitchAccount, account)
        row.tier = "pro"
        row.tier_expires_at = utcnow() - timedelta(seconds=1)
        assert remaining(db, row, "free", today_local()) == 3


def test_unlocks_are_fetched_in_one_go(engine, account):
    with Session(engine) as db:
        row = db.get(StatPitchAccount, account)
        day = today_local()
        unlock(db, row, "free", "fixture-a", day)
        unlock(db, row, "free", "fixture-b", day)

        assert unlocked_ids(db, row) == {"fixture-a", "fixture-b"}
        assert unlocked_ids(db, None) == set()


def test_deleting_an_account_takes_its_unlocks_with_it(engine, account):
    with Session(engine) as db:
        row = db.get(StatPitchAccount, account)
        unlock(db, row, "free", "fixture-a", today_local())

        db.delete(row)
        db.commit()

        assert db.exec(select(StatPitchPredictionUnlock)).all() == []


# ── Over the wire ────────────────────────────────────────────────────────────


def test_an_anonymous_visitor_sees_no_prediction(client, make_fixture, seed_fixtures):
    """Signing up is what reveals one — which makes registration the conversion
    step rather than an afterthought."""
    seed_fixtures(make_fixture())
    (body,) = client.get("/statpitch/fixtures/today").json()

    assert body["locked"] is True
    assert "home_win_prob" not in body
    # But the fixture itself is never hidden: teams, kickoff and crests are the
    # shape of the product, not the thing being sold.
    assert body["home_team"]
    assert "home_crest_url" in body


def test_listing_never_spends_an_unlock(client, signup, make_fixture, seed_fixtures):
    """Browsing what is on today is not the thing being sold."""
    seed_fixtures(make_fixture(), make_fixture(), make_fixture())
    signup()

    response = client.get("/statpitch/fixtures/today")
    assert response.headers[QUOTA_HEADER] == "3"
    assert all(row["locked"] for row in response.json())


def test_opening_a_fixture_reveals_it(client, signup, make_fixture, seed_fixtures):
    (fixture,) = seed_fixtures(make_fixture())
    signup()

    response = client.get(f"/statpitch/fixtures/{fixture.id}")
    body = response.json()

    assert body["locked"] is False
    assert body["home_win_prob"] is not None
    assert response.headers[QUOTA_HEADER] == "2"
    # Still free depth — the prediction, not the prices behind it.
    assert "odds_home" not in body


def test_an_unlocked_fixture_then_shows_in_the_list(client, signup, make_fixture, seed_fixtures):
    first, second = seed_fixtures(make_fixture(), make_fixture())
    signup()
    client.get(f"/statpitch/fixtures/{first.id}")

    by_id = {row["id"]: row for row in client.get("/statpitch/fixtures/today").json()}
    assert by_id[first.id]["locked"] is False
    assert by_id[second.id]["locked"] is True


def test_the_fourth_fixture_comes_back_locked_not_refused(
    client, signup, make_fixture, seed_fixtures
):
    """A 402 here would blank a screen the reader was already looking at."""
    fixtures = seed_fixtures(*[make_fixture() for _ in range(4)])
    signup()

    for fixture in fixtures[:3]:
        assert client.get(f"/statpitch/fixtures/{fixture.id}").json()["locked"] is False

    response = client.get(f"/statpitch/fixtures/{fixtures[3].id}")
    assert response.status_code == 200
    assert response.json()["locked"] is True
    assert "home_win_prob" not in response.json()
    assert response.headers[QUOTA_HEADER] == "0"


def test_a_spent_account_can_still_reopen_what_it_unlocked(
    client, signup, make_fixture, seed_fixtures
):
    fixtures = seed_fixtures(*[make_fixture() for _ in range(4)])
    signup()
    for fixture in fixtures[:3]:
        client.get(f"/statpitch/fixtures/{fixture.id}")
    client.get(f"/statpitch/fixtures/{fixtures[3].id}")

    assert client.get(f"/statpitch/fixtures/{fixtures[0].id}").json()["locked"] is False


def test_a_pro_caller_is_told_it_is_unlimited(client, signup, make_fixture, seed_fixtures):
    seed_fixtures(make_fixture())
    signup(tier="pro")

    response = client.get("/statpitch/fixtures/today")
    assert response.headers[QUOTA_HEADER] == "unlimited"
    assert all(row["locked"] is False for row in response.json())


def test_the_match_of_the_day_does_not_spend_an_unlock(client, signup, make_fixture, seed_fixtures):
    """It is its own line on the pricing page, beside the three — not one of
    them."""
    seed_fixtures(make_fixture(), make_fixture())
    signup()

    response = client.get("/statpitch/fixtures/today/best")
    assert response.json()["locked"] is False
    assert response.headers[QUOTA_HEADER] == "3"


def test_the_master_key_is_never_rationed(client, auth, make_fixture, seed_fixtures):
    (fixture,) = seed_fixtures(make_fixture())

    response = client.get(f"/statpitch/fixtures/{fixture.id}", headers=auth)
    assert response.headers[QUOTA_HEADER] == "unlimited"
    assert response.json()["locked"] is False
