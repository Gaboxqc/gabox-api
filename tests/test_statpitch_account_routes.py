"""The customer-facing account routes: register, login, password, trial.

`tests/test_statpitch_accounts.py` covers the machinery underneath; this covers
what a browser actually sees, including the two failure modes that are easy to
get subtly wrong — user enumeration and CSRF.
"""

from datetime import timedelta

import pytest
from sqlmodel import Session, select

from api.core.config import settings
from api.statpitch.accounts.models import StatPitchAccount, StatPitchAccountSession, utcnow

EMAIL = "bettor@example.com"
PASSWORD = "correct-horse-battery-staple"

SESSION_COOKIE = "statpitch_session"
CSRF_COOKIE = "statpitch_csrf"


@pytest.fixture(name="signup")
def signup_fixture(client):
    """Register an account and return the CSRF header its session expects.

    The cookie jar carries the session itself, exactly as a browser would; only
    the CSRF token has to be moved into a header by hand, which is what the
    frontend does with the value from the response body.
    """

    def _signup(email: str = EMAIL, password: str = PASSWORD):
        response = client.post(
            "/statpitch/accounts/register", json={"email": email, "password": password}
        )
        assert response.status_code == 201, response.text
        return {"X-CSRF-Token": response.json()["csrf_token"]}

    return _signup


# ── Registration ─────────────────────────────────────────────────────────────


def test_registering_signs_you_in_on_the_free_tier(client):
    response = client.post(
        "/statpitch/accounts/register", json={"email": EMAIL, "password": PASSWORD}
    )

    assert response.status_code == 201
    body = response.json()
    assert body["email"] == EMAIL
    assert body["tier"] == "free"
    assert body["trial_used"] is False
    assert body["csrf_token"]
    assert SESSION_COOKIE in response.cookies
    assert CSRF_COOKIE in response.cookies


def test_the_session_cookie_is_httponly_and_the_csrf_cookie_is_not(client):
    """XSS must not be able to read the session; the frontend must be able to
    read the CSRF token when it is same-origin."""
    response = client.post(
        "/statpitch/accounts/register", json={"email": EMAIL, "password": PASSWORD}
    )
    cookies = {
        cookie.split("=")[0]: cookie.lower() for cookie in response.headers.get_list("set-cookie")
    }
    assert "httponly" in cookies[SESSION_COOKIE]
    assert "httponly" not in cookies[CSRF_COOKIE]


def test_the_password_hash_never_leaves_the_server(client):
    response = client.post(
        "/statpitch/accounts/register", json={"email": EMAIL, "password": PASSWORD}
    )
    assert "password" not in response.text.lower()


def test_email_is_folded_before_it_is_stored(client, engine):
    """`Bettor@Example.COM` and `bettor@example.com` are one account."""
    client.post(
        "/statpitch/accounts/register",
        json={"email": "  Bettor@Example.COM  ", "password": PASSWORD},
    )

    with Session(engine) as db:
        stored = db.exec(select(StatPitchAccount)).all()
    assert [row.email for row in stored] == [EMAIL]


def test_the_same_address_in_a_different_case_is_a_conflict(client, signup):
    signup()
    response = client.post(
        "/statpitch/accounts/register", json={"email": "BETTOR@example.com", "password": PASSWORD}
    )
    assert response.status_code == 409


def test_a_short_password_is_refused(client):
    response = client.post(
        "/statpitch/accounts/register", json={"email": EMAIL, "password": "short"}
    )
    assert response.status_code == 422


def test_something_that_is_not_an_address_is_refused(client):
    response = client.post(
        "/statpitch/accounts/register", json={"email": "not-an-email", "password": PASSWORD}
    )
    assert response.status_code == 422


# ── Login ────────────────────────────────────────────────────────────────────


def test_login_returns_the_account_and_a_fresh_csrf_token(client, signup):
    signup()
    client.cookies.clear()

    response = client.post("/statpitch/accounts/login", json={"email": EMAIL, "password": PASSWORD})
    assert response.status_code == 200
    assert response.json()["email"] == EMAIL
    assert SESSION_COOKIE in response.cookies


def test_a_wrong_password_and_an_unknown_address_are_indistinguishable(client, signup):
    """Otherwise login becomes a way to discover who has an account here."""
    signup()
    client.cookies.clear()

    wrong = client.post(
        "/statpitch/accounts/login", json={"email": EMAIL, "password": "wrong-password-here"}
    )
    unknown = client.post(
        "/statpitch/accounts/login", json={"email": "nobody@example.com", "password": PASSWORD}
    )

    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


def test_a_deactivated_account_cannot_log_in(client, signup, engine):
    signup()
    client.cookies.clear()

    with Session(engine) as db:
        account = db.exec(select(StatPitchAccount)).first()
        account.is_active = False
        db.add(account)
        db.commit()

    response = client.post("/statpitch/accounts/login", json={"email": EMAIL, "password": PASSWORD})
    assert response.status_code == 401


def test_repeated_failures_lock_the_address_out(client, signup):
    signup()
    client.cookies.clear()

    for _ in range(settings.statpitch_login_max_attempts):
        client.post("/statpitch/accounts/login", json={"email": EMAIL, "password": "wrong-one!!!"})

    # Even the correct password is refused once the window is tripped.
    response = client.post("/statpitch/accounts/login", json={"email": EMAIL, "password": PASSWORD})
    assert response.status_code == 429
    assert "retry-after" in {key.lower() for key in response.headers}


# ── Me, logout, CSRF ─────────────────────────────────────────────────────────


def test_me_describes_the_signed_in_account(client, signup):
    signup()
    response = client.get("/statpitch/accounts/me")

    assert response.status_code == 200
    assert response.json()["email"] == EMAIL
    assert response.json()["tier"] == "free"


def test_me_needs_a_session(client):
    assert client.get("/statpitch/accounts/me").status_code == 401


def test_me_reports_an_expired_tier_as_free(client, signup, engine):
    """The expiry takes effect without logging anyone out."""
    signup()
    with Session(engine) as db:
        account = db.exec(select(StatPitchAccount)).first()
        account.tier = "pro"
        account.tier_expires_at = utcnow() - timedelta(seconds=1)
        db.add(account)
        db.commit()

    assert client.get("/statpitch/accounts/me").json()["tier"] == "free"


def test_an_unsafe_request_without_the_csrf_header_is_refused(client, signup):
    signup()
    response = client.post("/statpitch/accounts/logout")
    assert response.status_code == 403


def test_an_unsafe_request_with_the_wrong_csrf_token_is_refused(client, signup):
    signup()
    response = client.post(
        "/statpitch/accounts/logout", headers={"X-CSRF-Token": "not-the-right-token"}
    )
    assert response.status_code == 403


def test_logout_revokes_the_session(client, signup):
    csrf = signup()
    assert client.post("/statpitch/accounts/logout", headers=csrf).status_code == 204
    assert client.get("/statpitch/accounts/me").status_code == 401


def test_revoke_all_closes_every_session(client, signup, engine):
    csrf = signup()
    assert client.post("/statpitch/accounts/sessions/revoke-all", headers=csrf).status_code == 204

    with Session(engine) as db:
        rows = db.exec(select(StatPitchAccountSession)).all()
    assert all(row.revoked_at is not None for row in rows)


# ── Password ─────────────────────────────────────────────────────────────────


def test_changing_the_password_needs_the_current_one(client, signup):
    csrf = signup()
    response = client.post(
        "/statpitch/accounts/password",
        json={"current_password": "not-it-at-all", "new_password": "a-brand-new-passphrase"},
        headers=csrf,
    )
    assert response.status_code == 401


def test_the_new_password_must_clear_the_length_rule(client, signup):
    csrf = signup()
    response = client.post(
        "/statpitch/accounts/password",
        json={"current_password": PASSWORD, "new_password": "tiny"},
        headers=csrf,
    )
    assert response.status_code == 422


def test_changing_the_password_closes_the_old_sessions(client, signup, engine):
    """A reset after a suspected compromise that leaves the attacker signed in
    has achieved nothing."""
    csrf = signup()
    with Session(engine) as db:
        before = db.exec(select(StatPitchAccountSession)).all()
        stale_ids = [row.id for row in before]

    response = client.post(
        "/statpitch/accounts/password",
        json={"current_password": PASSWORD, "new_password": "a-brand-new-passphrase"},
        headers=csrf,
    )
    assert response.status_code == 200

    with Session(engine) as db:
        for session_id in stale_ids:
            assert db.get(StatPitchAccountSession, session_id).revoked_at is not None

    # The caller is handed a working session rather than being bounced to login.
    assert client.get("/statpitch/accounts/me").status_code == 200


def test_the_new_password_is_the_one_that_works(client, signup):
    csrf = signup()
    client.post(
        "/statpitch/accounts/password",
        json={"current_password": PASSWORD, "new_password": "a-brand-new-passphrase"},
        headers=csrf,
    )
    client.cookies.clear()

    assert (
        client.post(
            "/statpitch/accounts/login", json={"email": EMAIL, "password": PASSWORD}
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/statpitch/accounts/login",
            json={"email": EMAIL, "password": "a-brand-new-passphrase"},
        ).status_code
        == 200
    )


# ── Trial ────────────────────────────────────────────────────────────────────


def test_the_trial_grants_pro_for_fourteen_days(client, signup, engine):
    csrf = signup()
    response = client.post("/statpitch/accounts/trial", headers=csrf)

    assert response.status_code == 200
    body = response.json()
    assert body["tier"] == "pro"
    assert body["trial_used"] is True

    with Session(engine) as db:
        account = db.exec(select(StatPitchAccount)).first()
        assert account.tier_source == "trial"
        remaining = account.tier_expires_at - utcnow()
    assert timedelta(days=13) < remaining <= timedelta(days=14)


def test_the_trial_can_only_be_taken_once(client, signup, engine):
    """Even after it lapses — a second one has to be a deliberate grant."""
    csrf = signup()
    client.post("/statpitch/accounts/trial", headers=csrf)

    with Session(engine) as db:
        account = db.exec(select(StatPitchAccount)).first()
        account.tier_expires_at = utcnow() - timedelta(seconds=1)
        db.add(account)
        db.commit()

    assert client.get("/statpitch/accounts/me").json()["tier"] == "free"
    assert client.post("/statpitch/accounts/trial", headers=csrf).status_code == 409


def test_the_trial_is_refused_while_a_paid_tier_is_live(client, signup, engine):
    csrf = signup()
    with Session(engine) as db:
        account = db.exec(select(StatPitchAccount)).first()
        account.tier = "elite"
        db.add(account)
        db.commit()

    assert client.post("/statpitch/accounts/trial", headers=csrf).status_code == 409


# ── Isolation from the admin ─────────────────────────────────────────────────


def test_a_customer_session_cannot_write_to_the_portfolio(client, signup):
    csrf = signup()
    response = client.post("/portfolio/tags", json={"name": "Crystal"}, headers=csrf)
    assert response.status_code == 401


def test_an_admin_session_is_not_a_statpitch_account(client, login):
    """The dashboard cookie must not resolve to a customer, tier or otherwise."""
    login()
    assert client.get("/statpitch/accounts/me").status_code == 401


def test_the_master_key_is_not_a_statpitch_account(client, auth):
    """A machine key has no tier; treating it as one would make the entitlement
    rules untestable from the outside."""
    assert client.get("/statpitch/accounts/me", headers=auth).status_code == 401
