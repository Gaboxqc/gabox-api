"""Deciding trial requests.

The customer half is covered in `test_statpitch_account_routes.py`. This is the
queue, the decision, and the thing the whole change exists for: nobody gives
themselves a paid tier.
"""

from datetime import timedelta

import pytest
from sqlmodel import Session, select

from api.statpitch.accounts.models import StatPitchAccount, utcnow
from api.statpitch.admin.grants import StatPitchTierGrant
from api.statpitch.trials import TRIAL_DAYS, StatPitchTrialRequest

PASSWORD = "correct-horse-battery-staple"


@pytest.fixture(name="requested")
def requested_fixture(client, engine):
    """A signed-in customer with a pending request. Returns (account_id, csrf)."""

    def _requested(email: str = "bettor@example.com", message: str | None = None):
        client.cookies.clear()
        registered = client.post(
            "/statpitch/accounts/register", json={"email": email, "password": PASSWORD}
        )
        assert registered.status_code == 201, registered.text
        csrf = {"X-CSRF-Token": registered.json()["csrf_token"]}

        asked = client.post(
            "/statpitch/accounts/trial/request", json={"message": message}, headers=csrf
        )
        assert asked.status_code == 201, asked.text

        with Session(engine) as db:
            account = db.exec(
                select(StatPitchAccount).where(StatPitchAccount.email == email)
            ).first()
            return account.id, csrf

    return _requested


def _queue(client, auth, **params):
    return client.get("/statpitch/admin/trial-requests", params=params, headers=auth).json()


# ── The queue ────────────────────────────────────────────────────────────────


def test_a_request_appears_in_the_queue(client, auth, requested):
    requested(message="keen to try it")

    (row,) = _queue(client, auth)
    assert row["status"] == "pending"
    assert row["account_email"] == "bettor@example.com"
    assert row["message"] == "keen to try it"


def test_the_queue_is_oldest_first(client, auth, requested):
    """It is a queue: whoever has waited longest goes first, which is the
    opposite of how the account list reads."""
    requested(email="first@example.com")
    requested(email="second@example.com")

    assert [row["account_email"] for row in _queue(client, auth)] == [
        "first@example.com",
        "second@example.com",
    ]


def test_the_queue_shows_only_pending_by_default(client, auth, requested):
    account_id, _ = requested()
    (row,) = _queue(client, auth)
    client.post(f"/statpitch/admin/trial-requests/{row['id']}/decline", json={}, headers=auth)

    assert _queue(client, auth) == []
    assert len(_queue(client, auth, status="all")) == 1


# ── Approving ────────────────────────────────────────────────────────────────


def test_approving_grants_pro_for_fourteen_days(client, auth, requested, engine):
    account_id, _ = requested()
    (row,) = _queue(client, auth)

    response = client.post(
        f"/statpitch/admin/trial-requests/{row['id']}/approve", json={}, headers=auth
    )
    assert response.status_code == 200
    assert response.json()["status"] == "approved"

    with Session(engine) as db:
        account = db.get(StatPitchAccount, account_id)
        assert account.effective_tier == "pro"
        assert account.tier_source == "trial"
        remaining = account.tier_expires_at - utcnow()
    assert timedelta(days=TRIAL_DAYS - 1) < remaining <= timedelta(days=TRIAL_DAYS)


def test_approving_reaches_the_customer(client, auth, requested):
    """Signed in as them, through the real session."""
    account_id, csrf = requested()
    (row,) = _queue(client, auth)
    client.post(f"/statpitch/admin/trial-requests/{row['id']}/approve", json={}, headers=auth)

    assert client.get("/statpitch/accounts/me").json()["tier"] == "pro"


def test_an_approved_trial_lands_in_the_tier_history(client, auth, requested, engine):
    """Beside every other grant, rather than in a ledger only this feature knows
    how to read."""
    account_id, _ = requested()
    (row,) = _queue(client, auth)
    client.post(f"/statpitch/admin/trial-requests/{row['id']}/approve", json={}, headers=auth)

    (entry,) = client.get(f"/statpitch/admin/accounts/{account_id}/grants", headers=auth).json()
    assert entry["from_tier"] == "free"
    assert entry["to_tier"] == "pro"
    assert "trial" in entry["reason"].lower()

    with Session(engine) as db:
        assert len(db.exec(select(StatPitchTierGrant)).all()) == 1


def test_approving_marks_the_trial_used(client, auth, requested):
    account_id, csrf = requested()
    (row,) = _queue(client, auth)
    client.post(f"/statpitch/admin/trial-requests/{row['id']}/approve", json={}, headers=auth)

    assert client.get("/statpitch/accounts/me").json()["trial_used"] is True
    # And so they cannot ask again.
    assert (
        client.post("/statpitch/accounts/trial/request", json={}, headers=csrf).status_code == 409
    )


def test_a_decision_is_recorded_with_its_author(client, auth, requested):
    requested()
    (row,) = _queue(client, auth)
    client.post(
        f"/statpitch/admin/trial-requests/{row['id']}/approve",
        json={"reason": "known customer"},
        headers=auth,
    )

    (after,) = _queue(client, auth, status="all")
    assert after["decided_by"] == "api_key"
    assert after["decision_reason"] == "known customer"
    assert after["decided_at"] is not None


def test_a_request_cannot_be_decided_twice(client, auth, requested):
    """Re-deciding would overwrite who decided it and when, which is the part
    worth keeping."""
    requested()
    (row,) = _queue(client, auth)
    client.post(f"/statpitch/admin/trial-requests/{row['id']}/approve", json={}, headers=auth)

    again = client.post(
        f"/statpitch/admin/trial-requests/{row['id']}/approve", json={}, headers=auth
    )
    assert again.status_code == 409
    assert "already approved" in again.json()["detail"]


# ── Declining ────────────────────────────────────────────────────────────────


def test_declining_grants_nothing(client, auth, requested):
    account_id, _ = requested()
    (row,) = _queue(client, auth)

    response = client.post(
        f"/statpitch/admin/trial-requests/{row['id']}/decline",
        json={"reason": "not right now"},
        headers=auth,
    )
    assert response.status_code == 200
    assert response.json()["status"] == "declined"
    assert (
        client.get(f"/statpitch/admin/accounts/{account_id}", headers=auth).json()["tier"] == "free"
    )


def test_declining_lets_them_ask_again(client, auth, requested):
    """Being told no once is not the same as having had the trial."""
    account_id, csrf = requested()
    (row,) = _queue(client, auth)
    client.post(f"/statpitch/admin/trial-requests/{row['id']}/decline", json={}, headers=auth)

    assert (
        client.get(f"/statpitch/admin/accounts/{account_id}", headers=auth).json()["trial_used"]
        is False
    )
    assert (
        client.post("/statpitch/accounts/trial/request", json={}, headers=csrf).status_code == 201
    )


def test_a_declined_request_keeps_its_reason(client, auth, requested):
    account_id, csrf = requested()
    (row,) = _queue(client, auth)
    client.post(
        f"/statpitch/admin/trial-requests/{row['id']}/decline",
        json={"reason": "we spoke on email"},
        headers=auth,
    )

    latest = client.get("/statpitch/accounts/trial/request").json()
    assert latest["status"] == "declined"
    assert latest["decision_reason"] == "we spoke on email"


def test_declining_writes_no_grant(client, auth, requested, engine):
    requested()
    (row,) = _queue(client, auth)
    client.post(f"/statpitch/admin/trial-requests/{row['id']}/decline", json={}, headers=auth)

    with Session(engine) as db:
        assert db.exec(select(StatPitchTierGrant)).all() == []


def test_an_unknown_request_is_404(client, auth):
    assert (
        client.post(
            "/statpitch/admin/trial-requests/9999/approve", json={}, headers=auth
        ).status_code
        == 404
    )


def test_deleting_an_account_takes_its_requests_with_it(client, auth, requested, engine):
    account_id, _ = requested()
    client.delete(f"/statpitch/admin/accounts/{account_id}", headers=auth)

    with Session(engine) as db:
        assert db.exec(select(StatPitchTrialRequest)).all() == []


# ── Who may decide ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/statpitch/admin/trial-requests"),
        ("post", "/statpitch/admin/trial-requests/1/approve"),
        ("post", "/statpitch/admin/trial-requests/1/decline"),
    ],
)
def test_the_decision_routes_are_guarded(method, path, client):
    call = getattr(client, method)
    response = call(path) if method == "get" else call(path, json={})
    assert response.status_code in {401, 403}


def test_a_customer_cannot_approve_their_own_request(client, requested):
    """The point of removing the self-serve button. Asking is theirs; deciding
    is not."""
    account_id, csrf = requested()

    response = client.post("/statpitch/admin/trial-requests/1/approve", json={}, headers=csrf)
    assert response.status_code in {401, 403}
    assert client.get("/statpitch/accounts/me").json()["tier"] == "free"
