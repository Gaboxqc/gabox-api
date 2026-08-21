"""Administering customer accounts.

Half of this is the boundary: these routes must answer to an admin and to
nobody else, least of all to a customer who is signed in perfectly legitimately
somewhere else in the same module.
"""

import pytest
from sqlmodel import Session, select

from api.statpitch.accounts.models import StatPitchAccount, StatPitchAccountSession
from api.statpitch.accounts.sessions import create_session

PASSWORD = "correct-horse-battery-staple"


@pytest.fixture(name="customer")
def customer_fixture(client, engine):
    """Register a customer through the public route and return their id."""

    def _customer(email: str = "bettor@example.com", tier: str = "free"):
        client.cookies.clear()
        response = client.post(
            "/statpitch/accounts/register", json={"email": email, "password": PASSWORD}
        )
        assert response.status_code == 201, response.text

        with Session(engine) as db:
            account = db.exec(
                select(StatPitchAccount).where(StatPitchAccount.email == email)
            ).first()
            if tier != "free":
                account.tier = tier
                db.add(account)
                db.commit()
            return account.id

    return _customer


# ── Who may reach these at all ───────────────────────────────────────────────


def test_the_master_key_is_admitted(client, auth):
    assert client.get("/statpitch/admin/accounts", headers=auth).status_code == 200


def test_an_admin_session_is_admitted(client, login):
    login()
    assert client.get("/statpitch/admin/accounts").status_code == 200


def test_an_anonymous_caller_is_refused(client):
    assert client.get("/statpitch/admin/accounts").status_code == 401


def test_a_signed_in_customer_is_refused(client, customer):
    """The boundary this module exists to hold. A customer session is valid —
    it just is not an admin one, and must not become one by being pointed at a
    different path."""
    customer()
    assert client.get("/statpitch/admin/accounts").status_code == 401


def test_an_elite_customer_is_still_refused(client, customer):
    """Elite is the top tier a customer can buy. It buys API access, not
    administration."""
    customer(tier="elite")
    assert client.get("/statpitch/admin/accounts").status_code == 401


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/statpitch/admin/accounts"),
        ("get", "/statpitch/admin/accounts/1"),
        ("post", "/statpitch/admin/accounts"),
        ("patch", "/statpitch/admin/accounts/1"),
        ("delete", "/statpitch/admin/accounts/1"),
    ],
)
def test_every_route_is_guarded(method, path, client):
    """Nothing under /statpitch/admin answers without a credential, whatever the
    verb — a route added later that forgets the dependency fails here."""
    call = getattr(client, method)
    response = call(path) if method in {"get", "delete"} else call(path, json={})

    assert response.status_code in {401, 403}


# ── Browsing ─────────────────────────────────────────────────────────────────


def test_accounts_are_listed_with_their_tier(client, auth, customer):
    customer(email="one@example.com", tier="pro")

    (row,) = client.get("/statpitch/admin/accounts", headers=auth).json()
    assert row["email"] == "one@example.com"
    assert row["tier"] == "pro"
    assert row["effective_tier"] == "pro"
    assert row["is_active"] is True


def test_the_raw_and_effective_tier_are_both_reported(client, auth, customer, engine):
    """A lapsed Elite must not look like it was set to free — an admin about to
    extend it needs to see what was granted."""
    from datetime import timedelta

    from api.statpitch.accounts.models import utcnow

    account_id = customer(tier="elite")
    with Session(engine) as db:
        account = db.get(StatPitchAccount, account_id)
        account.tier_expires_at = utcnow() - timedelta(days=1)
        db.add(account)
        db.commit()

    row = client.get(f"/statpitch/admin/accounts/{account_id}", headers=auth).json()
    assert row["tier"] == "elite"
    assert row["effective_tier"] == "free"


def test_a_password_hash_is_never_returned(client, auth, customer):
    customer()
    body = client.get("/statpitch/admin/accounts", headers=auth).text
    assert "password" not in body.lower()


def test_live_sessions_are_counted(client, auth, customer, engine):
    account_id = customer()
    with Session(engine) as db:
        account = db.get(StatPitchAccount, account_id)
        create_session(db, account, ip_address=None, user_agent=None)

    row = client.get(f"/statpitch/admin/accounts/{account_id}", headers=auth).json()
    # One from registering, one opened here.
    assert row["active_sessions"] == 2


def test_the_list_is_filtered_and_counted(client, auth, customer):
    customer(email="pro@example.com", tier="pro")
    customer(email="free@example.com")

    response = client.get("/statpitch/admin/accounts", params={"tier": "pro"}, headers=auth)
    assert response.headers["X-Total-Count"] == "1"
    assert [row["email"] for row in response.json()] == ["pro@example.com"]


def test_the_list_can_be_searched_by_email(client, auth, customer):
    customer(email="gabriel@example.com")
    customer(email="someone@example.com")

    rows = client.get("/statpitch/admin/accounts", params={"email": "GABRIEL"}, headers=auth).json()
    assert [row["email"] for row in rows] == ["gabriel@example.com"]


def test_an_unknown_account_is_404(client, auth):
    assert client.get("/statpitch/admin/accounts/9999", headers=auth).status_code == 404


# ── Creating ─────────────────────────────────────────────────────────────────


def test_creating_an_account_returns_a_one_time_password(client, auth):
    response = client.post(
        "/statpitch/admin/accounts", json={"email": "new@example.com"}, headers=auth
    )

    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "new@example.com"
    assert body["tier"] == "free"
    assert len(body["temporary_password"]) >= 20


def test_the_generated_password_actually_works(client, auth):
    password = client.post(
        "/statpitch/admin/accounts", json={"email": "new@example.com"}, headers=auth
    ).json()["temporary_password"]

    client.cookies.clear()
    response = client.post(
        "/statpitch/accounts/login", json={"email": "new@example.com", "password": password}
    )
    assert response.status_code == 200


def test_the_password_is_shown_once_and_not_again(client, auth):
    created = client.post(
        "/statpitch/admin/accounts", json={"email": "new@example.com"}, headers=auth
    ).json()

    fetched = client.get(f"/statpitch/admin/accounts/{created['id']}", headers=auth).json()
    assert "temporary_password" not in fetched


def test_an_admin_cannot_choose_the_password(client, auth):
    """No password field on the request, so supplying one is ignored rather than
    honoured — a plaintext credential must not travel through an admin form."""
    body = client.post(
        "/statpitch/admin/accounts",
        json={"email": "new@example.com", "password": "hunter2hunter2"},
        headers=auth,
    ).json()

    client.cookies.clear()
    refused = client.post(
        "/statpitch/accounts/login",
        json={"email": "new@example.com", "password": "hunter2hunter2"},
    )
    assert refused.status_code == 401
    assert body["temporary_password"] != "hunter2hunter2"


def test_a_duplicate_email_is_a_conflict(client, auth, customer):
    customer(email="taken@example.com")
    response = client.post(
        "/statpitch/admin/accounts", json={"email": "taken@example.com"}, headers=auth
    )
    assert response.status_code == 409


def test_the_email_is_folded_on_the_way_in(client, auth):
    body = client.post(
        "/statpitch/admin/accounts", json={"email": "  New@Example.COM "}, headers=auth
    ).json()
    assert body["email"] == "new@example.com"


def test_something_that_is_not_an_address_is_refused(client, auth):
    response = client.post(
        "/statpitch/admin/accounts", json={"email": "not-an-email"}, headers=auth
    )
    assert response.status_code == 422


# ── Deactivating ─────────────────────────────────────────────────────────────


def test_deactivating_bars_login(client, auth, customer):
    customer(email="gone@example.com")
    account_id = 1

    response = client.patch(
        f"/statpitch/admin/accounts/{account_id}", json={"is_active": False}, headers=auth
    )
    assert response.status_code == 200
    assert response.json()["is_active"] is False

    client.cookies.clear()
    refused = client.post(
        "/statpitch/accounts/login", json={"email": "gone@example.com", "password": PASSWORD}
    )
    assert refused.status_code == 401


def test_deactivating_closes_live_sessions(client, auth, customer, engine):
    """Otherwise a disabled account carries on reading for up to thirty days,
    because a session is only checked against `is_active` when it is loaded."""
    account_id = customer()

    client.patch(f"/statpitch/admin/accounts/{account_id}", json={"is_active": False}, headers=auth)

    with Session(engine) as db:
        sessions = db.exec(
            select(StatPitchAccountSession).where(StatPitchAccountSession.account_id == account_id)
        ).all()
    assert sessions and all(row.revoked_at is not None for row in sessions)


def test_reactivating_is_possible(client, auth, customer):
    account_id = customer()

    client.patch(f"/statpitch/admin/accounts/{account_id}", json={"is_active": False}, headers=auth)
    response = client.patch(
        f"/statpitch/admin/accounts/{account_id}", json={"is_active": True}, headers=auth
    )

    assert response.json()["is_active"] is True


# ── Deleting ─────────────────────────────────────────────────────────────────


def test_deleting_removes_the_account(client, auth, customer, engine):
    account_id = customer()

    assert client.delete(f"/statpitch/admin/accounts/{account_id}", headers=auth).status_code == 204

    with Session(engine) as db:
        assert db.get(StatPitchAccount, account_id) is None


def test_deleting_takes_the_sessions_with_it(client, auth, customer, engine):
    account_id = customer()
    client.delete(f"/statpitch/admin/accounts/{account_id}", headers=auth)

    with Session(engine) as db:
        assert db.exec(select(StatPitchAccountSession)).all() == []


def test_deleting_something_that_is_not_there_is_404(client, auth):
    assert client.delete("/statpitch/admin/accounts/9999", headers=auth).status_code == 404
