"""Elite API keys: issuing, using, and turning off.

The properties worth guarding are the ones that make a leaked key survivable —
the secret is shown once and never stored, revocation is immediate, and a key
belonging to somebody else is invisible.
"""

import pytest
from sqlmodel import Session, select

from api.statpitch.accounts.keys import KEY_PREFIX, StatPitchApiKey, hash_key
from api.statpitch.accounts.models import StatPitchAccount

EMAIL = "bettor@example.com"
PASSWORD = "correct-horse-battery-staple"


@pytest.fixture(name="signup")
def signup_fixture(client, engine):
    def _signup(tier: str = "elite", email: str = EMAIL):
        client.cookies.clear()
        response = client.post(
            "/statpitch/accounts/register", json={"email": email, "password": PASSWORD}
        )
        assert response.status_code == 201, response.text

        with Session(engine) as db:
            account = db.exec(
                select(StatPitchAccount).where(StatPitchAccount.email == email)
            ).first()
            account.tier = tier
            db.add(account)
            db.commit()

        return {"X-CSRF-Token": response.json()["csrf_token"]}

    return _signup


@pytest.fixture(name="issued")
def issued_fixture(client, signup):
    """An Elite account with one live key. Returns the bearer header."""

    def _issued(name: str = "my bot"):
        csrf = signup("elite")
        response = client.post("/statpitch/accounts/keys", json={"name": name}, headers=csrf)
        assert response.status_code == 201, response.text
        return {"Authorization": f"Bearer {response.json()['key']}"}, csrf

    return _issued


# ── Issuing ──────────────────────────────────────────────────────────────────


def test_an_elite_account_can_issue_a_key(client, signup):
    csrf = signup("elite")
    response = client.post("/statpitch/accounts/keys", json={"name": "my bot"}, headers=csrf)

    assert response.status_code == 201
    body = response.json()
    assert body["key"].startswith(KEY_PREFIX)
    assert body["name"] == "my bot"
    assert body["revoked"] is False


def test_the_prefix_identifies_without_revealing(client, signup):
    csrf = signup("elite")
    body = client.post("/statpitch/accounts/keys", json={"name": "bot"}, headers=csrf).json()

    assert body["key"].startswith(body["prefix"])
    assert len(body["prefix"]) < len(body["key"])


def test_only_the_hash_is_stored(client, signup, engine):
    """A database leak must not hand over live credentials."""
    csrf = signup("elite")
    raw = client.post("/statpitch/accounts/keys", json={"name": "bot"}, headers=csrf).json()["key"]

    with Session(engine) as db:
        (stored,) = db.exec(select(StatPitchApiKey)).all()

    assert stored.key_hash == hash_key(raw)
    assert raw not in stored.key_hash


def test_the_key_is_shown_once_and_never_again(client, signup):
    """Nothing stores it, so a lost key is replaced rather than recovered."""
    csrf = signup("elite")
    client.post("/statpitch/accounts/keys", json={"name": "bot"}, headers=csrf)

    (listed,) = client.get("/statpitch/accounts/keys").json()
    assert "key" not in listed


@pytest.mark.parametrize("tier", ["free", "pro"])
def test_a_lesser_tier_cannot_issue_one(tier, client, signup):
    """API access is the whole of what Elite adds."""
    csrf = signup(tier)
    response = client.post("/statpitch/accounts/keys", json={"name": "bot"}, headers=csrf)

    assert response.status_code == 402
    assert "Elite" in response.json()["detail"]


def test_issuing_needs_a_session(client):
    assert client.post("/statpitch/accounts/keys", json={"name": "bot"}).status_code == 401


def test_issuing_needs_the_csrf_header(client, signup):
    signup("elite")
    assert client.post("/statpitch/accounts/keys", json={"name": "bot"}).status_code == 403


# ── Using ────────────────────────────────────────────────────────────────────


def test_a_key_reads_at_its_owners_tier(client, issued, make_fixture, seed_fixtures):
    seed_fixtures(make_fixture())
    bearer, _ = issued()
    client.cookies.clear()

    (body,) = client.get("/statpitch/fixtures/today", headers=bearer).json()
    assert body["locked"] is False
    assert "odds_home" in body


def test_a_key_reaches_the_paid_endpoints(client, issued):
    bearer, _ = issued()
    client.cookies.clear()

    assert client.get("/statpitch/ledger", headers=bearer).status_code == 200
    assert client.get("/statpitch/stats", headers=bearer).status_code == 200


def test_a_key_is_never_rationed(client, issued, make_fixture, seed_fixtures):
    (fixture,) = seed_fixtures(make_fixture())
    bearer, _ = issued()
    client.cookies.clear()

    response = client.get(f"/statpitch/fixtures/{fixture.id}", headers=bearer)
    assert response.headers["X-Predictions-Remaining"] == "unlimited"


def test_an_unknown_key_is_rejected(client):
    """Not silently downgraded to free: an integration whose key stopped working
    must be told, not left quietly reading teasers."""
    response = client.get(
        "/statpitch/fixtures/today", headers={"Authorization": f"Bearer {KEY_PREFIX}nonsense"}
    )
    assert response.status_code == 401


def test_something_that_is_not_one_of_our_keys_is_rejected(client):
    response = client.get(
        "/statpitch/fixtures/today", headers={"Authorization": "Bearer github_pat_whatever"}
    )
    assert response.status_code == 401


def test_no_key_at_all_is_simply_anonymous(client, make_fixture, seed_fixtures):
    """Presenting nothing is the free tier; presenting something broken is an
    error. The two must not be confused."""
    seed_fixtures(make_fixture())
    response = client.get("/statpitch/fixtures/today")

    assert response.status_code == 200
    assert response.json()[0]["locked"] is True


def test_using_a_key_records_that_it_was_used(client, issued, engine):
    bearer, _ = issued()
    client.cookies.clear()
    client.get("/statpitch/fixtures/today", headers=bearer)

    with Session(engine) as db:
        (key,) = db.exec(select(StatPitchApiKey)).all()
    assert key.last_used_at is not None


def test_a_key_stops_working_when_the_tier_lapses(client, issued, engine):
    """API access *is* the Elite line, so a lapsed subscription stops the key
    rather than quietly demoting it to free data."""
    bearer, _ = issued()
    client.cookies.clear()

    with Session(engine) as db:
        account = db.exec(select(StatPitchAccount)).first()
        account.tier = "pro"
        db.add(account)
        db.commit()

    response = client.get("/statpitch/fixtures/today", headers=bearer)
    assert response.status_code == 402


def test_a_key_stops_working_when_the_account_is_deactivated(client, issued, engine):
    bearer, _ = issued()
    client.cookies.clear()

    with Session(engine) as db:
        account = db.exec(select(StatPitchAccount)).first()
        account.is_active = False
        db.add(account)
        db.commit()

    assert client.get("/statpitch/fixtures/today", headers=bearer).status_code == 401


# ── Revoking ─────────────────────────────────────────────────────────────────


def test_revoking_takes_effect_immediately(client, issued):
    bearer, csrf = issued()
    (listed,) = client.get("/statpitch/accounts/keys").json()

    assert (
        client.delete(f"/statpitch/accounts/keys/{listed['id']}", headers=csrf).status_code == 204
    )

    client.cookies.clear()
    assert client.get("/statpitch/fixtures/today", headers=bearer).status_code == 401


def test_a_revoked_key_is_still_listed(client, issued):
    """One that turns up in a log later is worth being able to identify."""
    _, csrf = issued()
    (listed,) = client.get("/statpitch/accounts/keys").json()
    client.delete(f"/statpitch/accounts/keys/{listed['id']}", headers=csrf)

    (after,) = client.get("/statpitch/accounts/keys").json()
    assert after["revoked"] is True


def test_revoking_twice_is_harmless(client, issued):
    _, csrf = issued()
    (listed,) = client.get("/statpitch/accounts/keys").json()

    first = client.delete(f"/statpitch/accounts/keys/{listed['id']}", headers=csrf)
    second = client.delete(f"/statpitch/accounts/keys/{listed['id']}", headers=csrf)
    assert first.status_code == second.status_code == 204


def test_somebody_elses_key_is_invisible(client, issued, signup):
    """404 rather than 403 — the response must not confirm the id exists."""
    _, _ = issued()
    (listed,) = client.get("/statpitch/accounts/keys").json()

    other_csrf = signup("elite", email="someone@example.com")
    response = client.delete(f"/statpitch/accounts/keys/{listed['id']}", headers=other_csrf)
    assert response.status_code == 404


def test_a_lapsed_account_can_still_revoke(client, issued, engine):
    """Losing Elite must not strand keys it can no longer turn off."""
    _, csrf = issued()
    (listed,) = client.get("/statpitch/accounts/keys").json()

    with Session(engine) as db:
        account = db.exec(select(StatPitchAccount)).first()
        account.tier = "free"
        db.add(account)
        db.commit()

    assert (
        client.delete(f"/statpitch/accounts/keys/{listed['id']}", headers=csrf).status_code == 204
    )


def test_deleting_an_account_takes_its_keys_with_it(client, issued, engine):
    issued()
    with Session(engine) as db:
        account = db.exec(select(StatPitchAccount)).first()
        db.delete(account)
        db.commit()

        assert db.exec(select(StatPitchApiKey)).all() == []


# ── Isolation ────────────────────────────────────────────────────────────────


def test_a_customer_key_cannot_write_to_the_portfolio(client, issued):
    """It is a StatPitch read credential, not the master key."""
    bearer, _ = issued()
    client.cookies.clear()

    response = client.post("/portfolio/tags", json={"name": "Crystal"}, headers=bearer)
    assert response.status_code == 401


def test_a_customer_key_cannot_trigger_a_sync(client, issued):
    bearer, _ = issued()
    client.cookies.clear()

    assert client.post("/statpitch/sync", headers=bearer).status_code == 401
