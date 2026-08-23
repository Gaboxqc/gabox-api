"""Granting tiers, and keeping a record of it.

The tier column says what an account has. These tests are mostly about the other
half — that every change to it leaves something a human can read afterwards.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlmodel import Session, select

from api.statpitch.accounts.models import StatPitchAccount, utcnow
from api.statpitch.admin.grants import StatPitchTierGrant, as_naive_utc

PASSWORD = "correct-horse-battery-staple"


@pytest.fixture(name="created")
def created_fixture(client, auth):
    """An account created through the admin route, so tests start from free.

    Keeps the generated password, which is the only chance anything has to see
    it — it is returned once and never stored in a readable form.
    """
    body = client.post(
        "/statpitch/admin/accounts", json={"email": "bettor@example.com"}, headers=auth
    ).json()
    return body["id"], body["temporary_password"]


@pytest.fixture(name="account_id")
def account_id_fixture(created):
    return created[0]


def _grant(client, auth, account_id, **payload):
    body = {"tier": "pro", "reason": "paid for a month"} | payload
    return client.patch(f"/statpitch/admin/accounts/{account_id}/tier", json=body, headers=auth)


# ── Granting ─────────────────────────────────────────────────────────────────


def test_a_tier_can_be_granted(client, auth, account_id):
    response = _grant(client, auth, account_id, tier="elite")

    assert response.status_code == 200
    body = response.json()
    assert body["tier"] == "elite"
    assert body["effective_tier"] == "elite"
    assert body["tier_source"] == "manual"


def test_the_grant_reaches_the_product(client, auth, created, make_fixture, seed_fixtures):
    """The point of all of this: a granted tier changes what the customer sees.

    Signed in as them, through the real login, reading a real fixture — not
    inspecting the column that was just written.
    """
    account_id, password = created
    seed_fixtures(make_fixture())

    client.cookies.clear()
    login = client.post(
        "/statpitch/accounts/login",
        json={"email": "bettor@example.com", "password": password},
    )
    assert login.status_code == 200
    assert login.json()["tier"] == "free"

    (before,) = client.get("/statpitch/fixtures/today").json()
    assert "odds_home" not in before

    _grant(client, auth, account_id, tier="pro")

    (after,) = client.get("/statpitch/fixtures/today").json()
    assert after["locked"] is False
    assert "odds_home" in after


def test_an_expiry_is_stored(client, auth, account_id):
    until = datetime.now(UTC) + timedelta(days=30)
    body = _grant(client, auth, account_id, expires_at=until.isoformat()).json()

    assert body["tier_expires_at"] is not None
    assert body["effective_tier"] == "pro"


def test_no_expiry_means_perpetual(client, auth, account_id):
    body = _grant(client, auth, account_id, tier="elite").json()
    assert body["tier_expires_at"] is None
    assert body["effective_tier"] == "elite"


def test_an_expiry_already_past_is_refused(client, auth, account_id):
    """It would technically work — `effective_tier` would read free immediately —
    but nobody means that. It is a typo in a date."""
    gone = datetime.now(UTC) - timedelta(days=1)
    response = _grant(client, auth, account_id, expires_at=gone.isoformat())

    assert response.status_code == 422
    assert "past" in response.json()["detail"]


def test_an_offset_aware_expiry_is_folded_to_utc(client, auth, account_id, engine):
    """A JSON body may carry an offset. Storing it as-is would blow up the first
    time it was compared against a naive column."""
    until = "2027-01-01T00:00:00-06:00"
    client.patch(
        f"/statpitch/admin/accounts/{account_id}/tier",
        json={"tier": "pro", "reason": "offset test", "expires_at": until},
        headers=auth,
    )

    with Session(engine) as db:
        account = db.get(StatPitchAccount, account_id)
        assert account.tier_expires_at.tzinfo is None
        assert account.tier_expires_at.hour == 6  # -06:00 folded forward
        # And the comparison that would have raised now simply works.
        assert account.effective_tier == "pro"


def test_revoking_to_free_clears_the_expiry(client, auth, account_id):
    """A leftover date on a free account is harmless and confusing."""
    until = datetime.now(UTC) + timedelta(days=30)
    _grant(client, auth, account_id, expires_at=until.isoformat())

    body = _grant(client, auth, account_id, tier="free", reason="refunded").json()
    assert body["tier"] == "free"
    assert body["tier_expires_at"] is None


def test_an_unknown_tier_is_refused(client, auth, account_id):
    assert _grant(client, auth, account_id, tier="platinum").status_code == 422


def test_a_reason_is_required(client, auth, account_id):
    response = client.patch(
        f"/statpitch/admin/accounts/{account_id}/tier",
        json={"tier": "pro"},
        headers=auth,
    )
    assert response.status_code == 422


def test_an_empty_reason_is_refused(client, auth, account_id):
    assert _grant(client, auth, account_id, reason="  ").status_code == 422


def test_an_unknown_account_is_404(client, auth):
    assert _grant(client, auth, 9999).status_code == 404


# ── History ──────────────────────────────────────────────────────────────────


def test_a_grant_is_recorded(client, auth, account_id):
    _grant(client, auth, account_id, tier="pro", reason="paid for a month")

    (entry,) = client.get(f"/statpitch/admin/accounts/{account_id}/grants", headers=auth).json()
    assert entry["from_tier"] == "free"
    assert entry["to_tier"] == "pro"
    assert entry["reason"] == "paid for a month"
    assert entry["granted_by"]


def test_the_history_records_both_sides_of_the_change(client, auth, account_id):
    """So an entry reads on its own, without replaying every earlier row."""
    _grant(client, auth, account_id, tier="pro", reason="first")
    _grant(client, auth, account_id, tier="elite", reason="upgrade")

    entries = client.get(f"/statpitch/admin/accounts/{account_id}/grants", headers=auth).json()
    assert [(e["from_tier"], e["to_tier"]) for e in entries] == [
        ("pro", "elite"),
        ("free", "pro"),
    ]


def test_regranting_the_same_tier_is_an_extension(client, auth, account_id):
    """A renewal is not a mistake, and it leaves its own entry."""
    _grant(client, auth, account_id, tier="pro", reason="month one")
    _grant(client, auth, account_id, tier="pro", reason="month two")

    entries = client.get(f"/statpitch/admin/accounts/{account_id}/grants", headers=auth).json()
    assert [e["reason"] for e in entries] == ["month two", "month one"]


def test_the_history_is_append_only(client, auth, account_id, engine):
    _grant(client, auth, account_id, tier="pro", reason="first")
    _grant(client, auth, account_id, tier="free", reason="revoked")

    with Session(engine) as db:
        rows = db.exec(select(StatPitchTierGrant)).all()
    # Revoking does not erase the grant that came before it.
    assert len(rows) == 2


def test_who_granted_it_is_recorded(client, auth, account_id):
    _grant(client, auth, account_id)
    (entry,) = client.get(f"/statpitch/admin/accounts/{account_id}/grants", headers=auth).json()
    # The master key has no username, so it names itself.
    assert entry["granted_by"] == "api_key"


def test_an_admin_session_is_named_in_the_history(client, login, account_id, auth):
    csrf = login()
    client.patch(
        f"/statpitch/admin/accounts/{account_id}/tier",
        json={"tier": "pro", "reason": "by hand"},
        headers=csrf,
    )

    (entry,) = client.get(f"/statpitch/admin/accounts/{account_id}/grants", headers=csrf).json()
    assert entry["granted_by"] == "gabox"


def test_deleting_an_account_takes_its_history_with_it(client, auth, account_id, engine):
    _grant(client, auth, account_id)
    client.delete(f"/statpitch/admin/accounts/{account_id}", headers=auth)

    with Session(engine) as db:
        assert db.exec(select(StatPitchTierGrant)).all() == []


# ── Trial reset ──────────────────────────────────────────────────────────────


def test_resetting_the_trial_lets_it_be_taken_again(client, auth, account_id, engine):
    with Session(engine) as db:
        account = db.get(StatPitchAccount, account_id)
        account.trial_used_at = utcnow()
        db.add(account)
        db.commit()

    body = client.post(f"/statpitch/admin/accounts/{account_id}/trial/reset", headers=auth).json()
    assert body["trial_used"] is False
    assert body["trial_used_at"] is None


def test_resetting_the_trial_does_not_grant_a_tier(client, auth, account_id):
    """It restores the ability to *start* the trial. Somebody asking for a free
    month wants a grant, which is a different button and a different trail."""
    body = client.post(f"/statpitch/admin/accounts/{account_id}/trial/reset", headers=auth).json()

    assert body["tier"] == "free"
    assert body["effective_tier"] == "free"


def test_resetting_the_trial_writes_no_grant(client, auth, account_id, engine):
    client.post(f"/statpitch/admin/accounts/{account_id}/trial/reset", headers=auth)

    with Session(engine) as db:
        assert db.exec(select(StatPitchTierGrant)).all() == []


def test_resetting_an_unknown_account_is_404(client, auth):
    assert (
        client.post("/statpitch/admin/accounts/9999/trial/reset", headers=auth).status_code == 404
    )


# ── Guarding ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("patch", "/statpitch/admin/accounts/1/tier"),
        ("get", "/statpitch/admin/accounts/1/grants"),
        ("post", "/statpitch/admin/accounts/1/trial/reset"),
    ],
)
def test_the_tier_routes_are_guarded(method, path, client):
    call = getattr(client, method)
    response = call(path) if method == "get" else call(path, json={})
    assert response.status_code in {401, 403}


def test_a_customer_cannot_grant_themselves_a_tier(client):
    """The one that would matter most if it were wrong."""
    client.post(
        "/statpitch/accounts/register",
        json={"email": "sneaky@example.com", "password": PASSWORD},
    )
    response = client.patch(
        "/statpitch/admin/accounts/1/tier",
        json={"tier": "elite", "reason": "please"},
    )
    assert response.status_code in {401, 403}


# ── The helper ───────────────────────────────────────────────────────────────


def test_naive_timestamps_pass_through_unchanged():
    moment = datetime(2027, 1, 1, 12, 0)
    assert as_naive_utc(moment) is moment


def test_none_stays_none():
    assert as_naive_utc(None) is None
