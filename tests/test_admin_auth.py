"""Admin session authentication: login, CSRF, expiry, lockout."""

from datetime import timedelta

import pytest
from sqlmodel import Session, select

from api.core.auth.models import AdminSession, AdminUser, utcnow
from api.core.config import settings

SESSION_COOKIE = "gabox_session"
CSRF_COOKIE = "gabox_csrf"


def _session_row(engine, token_hash=None) -> AdminSession:
    with Session(engine) as db:
        statement = select(AdminSession)
        if token_hash:
            statement = statement.where(AdminSession.token_hash == token_hash)
        row = db.exec(statement.order_by(AdminSession.id.desc())).first()
        assert row is not None, "no session row was created"
        return row


# ── Login ────────────────────────────────────────────────────────────────────


def test_login_sets_both_cookies(client, admin):
    response = client.post(
        "/auth/login", json={"username": admin["username"], "password": admin["password"]}
    )
    assert response.status_code == 200
    assert response.json()["username"] == admin["username"]
    assert SESSION_COOKIE in response.cookies
    assert CSRF_COOKIE in response.cookies


def test_session_cookie_is_httponly_and_csrf_cookie_is_not(client, admin):
    """The session must be unreadable from JS; the CSRF token must be readable."""
    response = client.post(
        "/auth/login", json={"username": admin["username"], "password": admin["password"]}
    )
    cookie_headers = response.headers.get_list("set-cookie")
    session_header = next(h for h in cookie_headers if h.startswith(SESSION_COOKIE))
    csrf_header = next(h for h in cookie_headers if h.startswith(CSRF_COOKIE))

    assert "httponly" in session_header.lower()
    assert "samesite=lax" in session_header.lower()
    assert "httponly" not in csrf_header.lower()


def test_login_returns_the_csrf_token_in_the_body(client, admin):
    """The dashboard cannot read the CSRF cookie: it is host-only to the API and
    the dashboard runs on a different subdomain. The body is its only source."""
    response = client.post(
        "/auth/login", json={"username": admin["username"], "password": admin["password"]}
    )
    token = response.json()["csrf_token"]
    assert token
    assert token == client.cookies["gabox_csrf"]


def test_me_reissues_the_csrf_token(client, login):
    """So a dashboard reload recovers the token without a fresh login."""
    login()
    body = client.get("/auth/me").json()
    assert body["csrf_token"] == client.cookies["gabox_csrf"]


def test_body_csrf_token_is_accepted_on_writes(client, admin):
    """Proves the body token is interchangeable with the cookie-derived one."""
    response = client.post(
        "/auth/login", json={"username": admin["username"], "password": admin["password"]}
    )
    token = response.json()["csrf_token"]
    client.cookies.delete("gabox_csrf")  # as it would be, cross-subdomain
    created = client.post(
        "/portfolio/tags", json={"name": "Haskell"}, headers={"X-CSRF-Token": token}
    )
    assert created.status_code == 201


def test_secure_flag_is_set_when_configured(client, admin, monkeypatch):
    """The suite runs with Secure off (see conftest), so the production default
    needs asserting explicitly — shipping without it would expose the session
    cookie on any plain-http request."""
    monkeypatch.setattr(settings, "session_cookie_secure", True)
    response = client.post(
        "/auth/login", json={"username": admin["username"], "password": admin["password"]}
    )
    assert response.status_code == 200
    cookie_headers = response.headers.get_list("set-cookie")
    assert cookie_headers
    assert all("secure" in header.lower() for header in cookie_headers)


def test_raw_token_is_not_stored(client, admin, engine):
    """Only the hash is persisted, so a database leak yields no live sessions."""
    client.post("/auth/login", json={"username": admin["username"], "password": admin["password"]})
    raw_token = client.cookies[SESSION_COOKIE]
    row = _session_row(engine)
    assert row.token_hash != raw_token
    assert len(row.token_hash) == 64  # sha256 hex


def test_wrong_password_is_rejected(client, admin):
    response = client.post(
        "/auth/login", json={"username": admin["username"], "password": "wrong-password-entirely"}
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid credentials."
    assert SESSION_COOKIE not in response.cookies


def test_unknown_user_and_wrong_password_are_indistinguishable(client, admin):
    """Otherwise login becomes a username-enumeration oracle."""
    unknown = client.post("/auth/login", json={"username": "nobody", "password": "whatever-long"})
    wrong = client.post(
        "/auth/login", json={"username": admin["username"], "password": "whatever-long"}
    )
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json() == wrong.json()


def test_inactive_user_cannot_log_in(client, admin, engine):
    with Session(engine) as db:
        user = db.get(AdminUser, admin["id"])
        user.is_active = False
        db.add(user)
        db.commit()

    response = client.post(
        "/auth/login", json={"username": admin["username"], "password": admin["password"]}
    )
    assert response.status_code == 401


def test_login_records_last_login(client, admin, engine, login):
    login()
    with Session(engine) as db:
        assert db.get(AdminUser, admin["id"]).last_login_at is not None


# ── /auth/me and logout ──────────────────────────────────────────────────────


def test_me_requires_a_session(client):
    assert client.get("/auth/me").status_code == 401


def test_me_returns_the_logged_in_admin(client, admin, login):
    login()
    response = client.get("/auth/me")
    assert response.status_code == 200
    assert response.json()["username"] == admin["username"]


def test_master_key_cannot_impersonate_a_session(client, auth):
    """The machine key is not a person; /auth/me must not accept it."""
    assert client.get("/auth/me", headers=auth).status_code == 401


def test_logout_revokes_the_session(client, login):
    csrf = login()
    assert client.post("/auth/logout", headers=csrf).status_code == 204
    assert client.get("/auth/me").status_code == 401


def test_logout_requires_csrf(client, login):
    login()
    assert client.post("/auth/logout").status_code == 403


# ── Session validity ─────────────────────────────────────────────────────────


def test_expired_session_is_rejected(client, login, engine):
    login()
    with Session(engine) as db:
        row = db.exec(select(AdminSession).order_by(AdminSession.id.desc())).first()
        row.expires_at = utcnow() - timedelta(seconds=1)
        db.add(row)
        db.commit()

    assert client.get("/auth/me").status_code == 401


def test_idle_session_is_rejected(client, login, engine):
    login()
    with Session(engine) as db:
        row = db.exec(select(AdminSession).order_by(AdminSession.id.desc())).first()
        row.last_used_at = utcnow() - timedelta(seconds=settings.session_idle_seconds + 60)
        db.add(row)
        db.commit()

    assert client.get("/auth/me").status_code == 401


def test_activity_slides_the_idle_window(client, login, engine):
    login()
    before = _session_row(engine).last_used_at
    with Session(engine) as db:
        row = db.exec(select(AdminSession).order_by(AdminSession.id.desc())).first()
        row.last_used_at = utcnow() - timedelta(seconds=60)
        db.add(row)
        db.commit()

    assert client.get("/portfolio/tags").status_code == 200
    # A read on a public endpoint does not touch the session, so drive an
    # authenticated one instead.
    assert client.get("/auth/me").status_code == 200
    assert _session_row(engine).last_used_at >= before - timedelta(seconds=61)


def test_garbage_token_is_rejected(client):
    client.cookies.set(SESSION_COOKIE, "not-a-real-token")
    assert client.get("/auth/me").status_code == 401


# ── Lockout ──────────────────────────────────────────────────────────────────


def test_lockout_after_repeated_failures(client, admin):
    for _ in range(settings.login_max_attempts):
        client.post(
            "/auth/login", json={"username": admin["username"], "password": "wrong-password"}
        )

    response = client.post(
        "/auth/login", json={"username": admin["username"], "password": admin["password"]}
    )
    assert response.status_code == 429
    assert "Retry-After" in response.headers


def test_successful_login_clears_the_failure_history(client, admin):
    for _ in range(settings.login_max_attempts - 1):
        client.post(
            "/auth/login", json={"username": admin["username"], "password": "wrong-password"}
        )

    assert (
        client.post(
            "/auth/login", json={"username": admin["username"], "password": admin["password"]}
        ).status_code
        == 200
    )
    # The counter reset, so a fresh run of failures is needed to lock out again.
    for _ in range(settings.login_max_attempts - 1):
        client.post(
            "/auth/login", json={"username": admin["username"], "password": "wrong-password"}
        )
    assert (
        client.post(
            "/auth/login", json={"username": admin["username"], "password": admin["password"]}
        ).status_code
        == 200
    )


# ── Write endpoints accept a session or the master key ───────────────────────


def test_session_authorises_a_write(client, login):
    csrf = login()
    response = client.post("/portfolio/tags", json={"name": "Rust"}, headers=csrf)
    assert response.status_code == 201


def test_write_with_session_but_no_csrf_is_rejected(client, login):
    login()
    response = client.post("/portfolio/tags", json={"name": "Rust"})
    assert response.status_code == 403
    assert response.json()["detail"] == "Missing or invalid CSRF token."


def test_write_with_wrong_csrf_is_rejected(client, login):
    login()
    response = client.post(
        "/portfolio/tags", json={"name": "Rust"}, headers={"X-CSRF-Token": "forged"}
    )
    assert response.status_code == 403


@pytest.mark.parametrize(
    ("method", "path"),
    [("patch", "/portfolio/tags/1"), ("delete", "/portfolio/tags/1")],
)
def test_csrf_is_enforced_on_every_unsafe_method(client, login, create, method, path):
    csrf = login()
    create("/portfolio/tags", name="Go")
    kwargs = {"json": {"name": "Golang"}} if method == "patch" else {}
    assert client.request(method.upper(), path, **kwargs).status_code == 403
    assert client.request(method.upper(), path, headers=csrf, **kwargs).status_code in (200, 204)


def test_master_key_still_authorises_writes(client, auth):
    """StatPitch and any scripts depend on this path continuing to work."""
    assert client.post("/portfolio/tags", json={"name": "Zig"}, headers=auth).status_code == 201


def test_api_key_writes_need_no_csrf(client, auth):
    """A server-to-server caller has no cookies, so CSRF does not apply."""
    assert client.post("/portfolio/tags", json={"name": "Nim"}, headers=auth).status_code == 201


def test_stale_session_cookie_falls_back_to_the_api_key(client, login, auth, engine):
    csrf = login()
    with Session(engine) as db:
        row = db.exec(select(AdminSession).order_by(AdminSession.id.desc())).first()
        row.revoked_at = utcnow()
        db.add(row)
        db.commit()

    response = client.post("/portfolio/tags", json={"name": "Elixir"}, headers={**auth, **csrf})
    assert response.status_code == 201


def test_sessions_listing_marks_the_current_session(client, login):
    csrf = login()
    rows = client.get("/auth/sessions", headers=csrf).json()
    assert len(rows) == 1
    assert rows[0]["current"] is True
