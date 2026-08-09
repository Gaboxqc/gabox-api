"""Security headers, the audit trail, and revoking every session."""

from sqlmodel import Session, select

from api.core.audit import AuditLogEntry

# ── Security headers ─────────────────────────────────────────────────────────


def test_headers_are_present_on_every_response(client):
    response = client.get("/portfolio/tags")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert "camera=()" in response.headers["Permissions-Policy"]


def test_auth_responses_are_not_cacheable(client, admin):
    """These carry the username and CSRF token; a cache must not keep them."""
    response = client.post(
        "/auth/login", json={"username": admin["username"], "password": admin["password"]}
    )
    assert response.headers["Cache-Control"] == "no-store"


def test_public_reads_stay_cacheable(client):
    """no-store is scoped to /auth so the public site keeps its caching."""
    assert client.get("/portfolio/tags").headers.get("Cache-Control") != "no-store"


def test_hsts_is_not_sent_over_plain_http(client):
    """Browsers ignore it anyway, and sending it would be misleading locally."""
    assert "Strict-Transport-Security" not in client.get("/portfolio/tags").headers


def test_cors_headers_survive_the_added_middleware(client):
    """Security middleware sits inside CORS, so preflight must still work."""
    response = client.options(
        "/portfolio/tags",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"


# ── Audit log ────────────────────────────────────────────────────────────────


def _entries(engine) -> list[AuditLogEntry]:
    with Session(engine) as db:
        return list(db.exec(select(AuditLogEntry).order_by(AuditLogEntry.id)).all())


def test_write_via_master_key_is_recorded(client, auth, engine):
    assert client.post("/portfolio/tags", json={"name": "Rust"}, headers=auth).status_code == 201

    entries = _entries(engine)
    assert len(entries) == 1
    assert (entries[0].method, entries[0].path, entries[0].status_code) == (
        "POST",
        "/portfolio/tags",
        201,
    )
    assert entries[0].principal_kind == "api_key"
    # The master key is not a person, so there is no username to attribute.
    assert entries[0].username is None


def test_write_via_session_is_attributed_to_the_admin(client, login, admin, engine):
    csrf = login()
    assert client.post("/portfolio/tags", json={"name": "Go"}, headers=csrf).status_code == 201

    entries = _entries(engine)
    assert len(entries) == 1
    assert entries[0].principal_kind == "session"
    assert entries[0].username == admin["username"]


def test_reads_are_not_audited(client, engine):
    client.get("/portfolio/tags")
    client.get("/portfolio/projects")
    assert _entries(engine) == []


def test_rejected_writes_are_not_audited(client, engine):
    """A failed write changed nothing, and logging probes would only let an
    attacker inflate the table."""
    assert client.post("/portfolio/tags", json={"name": "Nope"}).status_code == 401
    assert _entries(engine) == []


def test_login_is_not_audited_here(client, admin, engine):
    """Login already has admin_login_attempt; duplicating it would put the one
    endpoint that receives a password next to a general-purpose logger."""
    client.post("/auth/login", json={"username": admin["username"], "password": admin["password"]})
    assert _entries(engine) == []


def test_delete_is_recorded(client, auth, create, engine):
    tag = create("/portfolio/tags", name="Temporary")
    assert client.delete(f"/portfolio/tags/{tag['id']}", headers=auth).status_code == 204

    methods = [(entry.method, entry.status_code) for entry in _entries(engine)]
    assert ("DELETE", 204) in methods


def test_audit_records_no_request_body_or_query_string(client, auth, engine):
    """The table has nowhere to put one, which is the point — no endpoint can
    later leak a credential into it by accident."""
    client.post("/portfolio/tags?some=query", json={"name": "Zig"}, headers=auth)
    entry = _entries(engine)[0]
    assert entry.path == "/portfolio/tags"
    assert not hasattr(entry, "body")
    assert "some=query" not in entry.path


# ── Revoking every session ───────────────────────────────────────────────────


def test_revoke_all_signs_out_the_current_session_too(client, login):
    csrf = login()
    assert client.post("/auth/sessions/revoke-all", headers=csrf).status_code == 204
    # Deliberate: after a suspected compromise the safe end state is everything
    # closed, including the browser that pressed the button.
    assert client.get("/auth/me").status_code == 401


def test_revoke_all_closes_other_sessions(client, admin, engine):
    from api.core.auth.models import AdminSession

    # Two independent logins, as two browsers would produce.
    first = client.post(
        "/auth/login", json={"username": admin["username"], "password": admin["password"]}
    )
    first_token = first.cookies["gabox_session"]

    second_csrf = client.post(
        "/auth/login", json={"username": admin["username"], "password": admin["password"]}
    ).json()["csrf_token"]

    client.post("/auth/sessions/revoke-all", headers={"X-CSRF-Token": second_csrf})

    with Session(engine) as db:
        live = db.exec(
            select(AdminSession).where(AdminSession.revoked_at.is_(None))  # noqa: E711
        ).all()
        assert live == []

    # The first browser's cookie is dead too.
    client.cookies.set("gabox_session", first_token)
    assert client.get("/auth/me").status_code == 401


def test_revoke_all_requires_a_session_and_csrf(client, login):
    assert client.post("/auth/sessions/revoke-all").status_code == 401
    login()
    assert client.post("/auth/sessions/revoke-all").status_code == 403
