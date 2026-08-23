"""Seeing and closing a customer's sessions and keys.

These are the support-desk routes: somebody rings up saying their account is
being used by someone else, or that a key ended up in a public repository.
"""

from datetime import timedelta

import pytest
from sqlmodel import Session, select

from api.statpitch.accounts.models import StatPitchAccount, StatPitchAccountSession, utcnow
from api.statpitch.accounts.sessions import create_session

PASSWORD = "correct-horse-battery-staple"


@pytest.fixture(name="customer")
def customer_fixture(client, engine):
    """A registered customer, left signed in. Returns their id."""

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
            return account.id, response.json()["csrf_token"]

    return _customer


# ── Sessions ─────────────────────────────────────────────────────────────────


def test_sessions_are_listed(client, auth, customer):
    account_id, _ = customer()

    (row,) = client.get(f"/statpitch/admin/accounts/{account_id}/sessions", headers=auth).json()
    assert row["live"] is True
    assert row["revoked"] is False
    assert "created_at" in row


def test_no_token_is_ever_returned(client, auth, customer):
    """Only the hash was stored, and neither it nor the CSRF value belongs in a
    response."""
    account_id, _ = customer()

    body = client.get(f"/statpitch/admin/accounts/{account_id}/sessions", headers=auth).text
    assert "token" not in body.lower()
    assert "csrf" not in body.lower()


def test_where_and_when_are_reported(client, auth, customer, engine):
    """What makes a session recognisable to whoever opened it."""
    account_id, _ = customer()
    with Session(engine) as db:
        account = db.get(StatPitchAccount, account_id)
        create_session(db, account, ip_address="203.0.113.7", user_agent="Firefox")

    rows = client.get(f"/statpitch/admin/accounts/{account_id}/sessions", headers=auth).json()
    assert any(row["ip_address"] == "203.0.113.7" for row in rows)
    assert any(row["user_agent"] == "Firefox" for row in rows)


def test_closed_sessions_are_still_listed(client, auth, customer, engine):
    """ "Somebody was signed in from an address I do not recognise" is answered by
    the history, not by whatever happens to still be open."""
    account_id, _ = customer()
    with Session(engine) as db:
        session = db.exec(select(StatPitchAccountSession)).first()
        session.revoked_at = utcnow()
        db.add(session)
        db.commit()

    (row,) = client.get(f"/statpitch/admin/accounts/{account_id}/sessions", headers=auth).json()
    assert row["revoked"] is True
    assert row["live"] is False


def test_an_expired_session_is_not_live(client, auth, customer, engine):
    account_id, _ = customer()
    with Session(engine) as db:
        session = db.exec(select(StatPitchAccountSession)).first()
        session.expires_at = utcnow() - timedelta(seconds=1)
        db.add(session)
        db.commit()

    (row,) = client.get(f"/statpitch/admin/accounts/{account_id}/sessions", headers=auth).json()
    assert row["live"] is False
    assert row["revoked"] is False


def test_revoking_all_sessions_signs_them_out(client, auth, customer):
    account_id, _ = customer()

    response = client.post(
        f"/statpitch/admin/accounts/{account_id}/sessions/revoke-all", headers=auth
    )
    assert response.status_code == 200
    assert response.json()["active_sessions"] == 0

    # The customer's own cookie is now worthless.
    assert client.get("/statpitch/accounts/me").status_code == 401


def test_revoking_sessions_leaves_the_account_usable(client, auth, customer):
    """It stops the sessions, not the person. Barring them is a different call."""
    account_id, _ = customer()
    client.post(f"/statpitch/admin/accounts/{account_id}/sessions/revoke-all", headers=auth)

    client.cookies.clear()
    response = client.post(
        "/statpitch/accounts/login", json={"email": "bettor@example.com", "password": PASSWORD}
    )
    assert response.status_code == 200


def test_sessions_of_an_unknown_account_are_404(client, auth):
    assert client.get("/statpitch/admin/accounts/9999/sessions", headers=auth).status_code == 404
    assert (
        client.post("/statpitch/admin/accounts/9999/sessions/revoke-all", headers=auth).status_code
        == 404
    )


# ── Keys ─────────────────────────────────────────────────────────────────────


@pytest.fixture(name="with_key")
def with_key_fixture(client, customer):
    """An Elite customer holding one live API key."""

    def _with_key():
        account_id, csrf = customer(tier="elite")
        issued = client.post(
            "/statpitch/accounts/keys", json={"name": "their bot"}, headers={"X-CSRF-Token": csrf}
        )
        assert issued.status_code == 201, issued.text
        return account_id, issued.json()["key"]

    return _with_key


def test_keys_are_listed(client, auth, with_key):
    account_id, _ = with_key()

    (row,) = client.get(f"/statpitch/admin/accounts/{account_id}/keys", headers=auth).json()
    assert row["name"] == "their bot"
    assert row["revoked"] is False
    assert row["prefix"].startswith("sp_live_")


def test_the_key_itself_is_never_returned(client, auth, with_key):
    account_id, raw = with_key()

    body = client.get(f"/statpitch/admin/accounts/{account_id}/keys", headers=auth).text
    assert raw not in body


def test_a_key_can_be_revoked_on_their_behalf(client, auth, with_key):
    """For the call that starts "my key is in a public repo"."""
    account_id, raw = with_key()
    (row,) = client.get(f"/statpitch/admin/accounts/{account_id}/keys", headers=auth).json()

    assert client.delete(f"/statpitch/admin/keys/{row['id']}", headers=auth).status_code == 204

    client.cookies.clear()
    refused = client.get("/statpitch/fixtures/today", headers={"Authorization": f"Bearer {raw}"})
    assert refused.status_code == 401


def test_a_revoked_key_is_still_listed(client, auth, with_key):
    account_id, _ = with_key()
    (row,) = client.get(f"/statpitch/admin/accounts/{account_id}/keys", headers=auth).json()
    client.delete(f"/statpitch/admin/keys/{row['id']}", headers=auth)

    (after,) = client.get(f"/statpitch/admin/accounts/{account_id}/keys", headers=auth).json()
    assert after["revoked"] is True


def test_revoking_a_key_twice_is_harmless(client, auth, with_key):
    account_id, _ = with_key()
    (row,) = client.get(f"/statpitch/admin/accounts/{account_id}/keys", headers=auth).json()

    first = client.delete(f"/statpitch/admin/keys/{row['id']}", headers=auth)
    second = client.delete(f"/statpitch/admin/keys/{row['id']}", headers=auth)
    assert first.status_code == second.status_code == 204


def test_an_unknown_key_is_404(client, auth):
    assert client.delete("/statpitch/admin/keys/9999", headers=auth).status_code == 404


def test_keys_of_an_unknown_account_are_404(client, auth):
    assert client.get("/statpitch/admin/accounts/9999/keys", headers=auth).status_code == 404


# ── Guarding ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/statpitch/admin/accounts/1/sessions"),
        ("post", "/statpitch/admin/accounts/1/sessions/revoke-all"),
        ("get", "/statpitch/admin/accounts/1/keys"),
        ("delete", "/statpitch/admin/keys/1"),
    ],
)
def test_the_new_routes_are_guarded(method, path, client):
    call = getattr(client, method)
    response = call(path) if method in {"get", "delete"} else call(path, json={})
    assert response.status_code in {401, 403}


def test_a_customer_cannot_read_another_accounts_sessions(client, customer):
    """Signed in, and still nowhere near this."""
    customer()
    assert client.get("/statpitch/admin/accounts/1/sessions").status_code == 401
