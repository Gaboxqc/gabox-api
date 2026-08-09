"""Admin authentication endpoints."""

import logging

from fastapi import APIRouter, HTTPException, Request, Response, status
from sqlmodel import select

from api.core.auth.deps import SessionDependency
from api.core.auth.models import AdminRead, AdminSession, AdminUser, LoginRequest
from api.core.auth.passwords import verify_password, waste_time_like_a_verification
from api.core.auth.sessions import (
    clear_failures,
    create_session,
    is_locked_out,
    prune_old_attempts,
    record_attempt,
    revoke_all_sessions,
    revoke_session,
)
from api.core.config import settings
from api.core.database import SessionDep

log = logging.getLogger("api.auth")

router = APIRouter(prefix="/auth", tags=["Admin: Authentication"])

# Same body for "no such user" and "wrong password" — anything else turns login
# into a way to discover valid usernames.
_INVALID_CREDENTIALS = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Invalid credentials.",
)


def _client_ip(request: Request) -> str:
    """Caller's address, preferring the proxy header Vercel sets.

    `X-Forwarded-For` is client-controllable, so it is only believed when
    `trust_proxy_headers` says a proxy is overwriting it. Exposed directly, an
    attacker could otherwise forge the header and sidestep the lockout entirely
    by presenting a fresh address on each attempt.
    """
    if settings.trust_proxy_headers:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _set_auth_cookies(response: Response, raw_token: str, csrf_token: str) -> None:
    response.set_cookie(
        key=settings.session_cookie_name,
        value=raw_token,
        max_age=settings.session_max_age_seconds,
        httponly=True,  # unreadable from JavaScript, so XSS cannot exfiltrate it
        secure=settings.session_cookie_secure,
        samesite=settings.session_cookie_samesite,
        path="/",
    )
    response.set_cookie(
        key=settings.csrf_cookie_name,
        value=csrf_token,
        max_age=settings.session_max_age_seconds,
        # Readable rather than httpOnly, but the dashboard takes the token from
        # the login/me response body instead: this cookie is host-only to the
        # API, so JavaScript on another subdomain cannot see it. Kept for
        # same-origin deployments and as a double-submit signal.
        httponly=False,
        secure=settings.session_cookie_secure,
        samesite=settings.session_cookie_samesite,
        path="/",
    )


def _clear_auth_cookies(response: Response) -> None:
    for name in (settings.session_cookie_name, settings.csrf_cookie_name):
        response.delete_cookie(
            key=name,
            path="/",
            secure=settings.session_cookie_secure,
            samesite=settings.session_cookie_samesite,
        )


@router.post(
    "/login",
    response_model=AdminRead,
    operation_id="admin_login",
    summary="Log in and open an admin session",
)
async def login(
    credentials: LoginRequest,
    request: Request,
    response: Response,
    db: SessionDep,
):
    ip_address = _client_ip(request)
    username = credentials.username.strip()

    if is_locked_out(db, username, ip_address):
        log.warning("Locked-out login attempt for %r from %s", username, ip_address)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed attempts. Try again later.",
            headers={"Retry-After": str(settings.login_attempt_window_seconds)},
        )

    user = db.exec(select(AdminUser).where(AdminUser.username == username)).first()

    if user is None:
        # Burn the same CPU a real verification would, then fail identically.
        waste_time_like_a_verification()
        record_attempt(db, username, ip_address, succeeded=False)
        raise _INVALID_CREDENTIALS

    matched, rehashed = verify_password(user.password_hash, credentials.password)

    # An inactive account is checked after the password so that disabling an
    # account does not make it respond differently to a wrong password.
    if not matched or not user.is_active:
        record_attempt(db, username, ip_address, succeeded=False)
        raise _INVALID_CREDENTIALS

    if rehashed:
        user.password_hash = rehashed
        db.add(user)
        db.commit()

    record_attempt(db, username, ip_address, succeeded=True)
    clear_failures(db, username, ip_address)
    prune_old_attempts(db)

    session, raw_token = create_session(
        db,
        user,
        ip_address=ip_address,
        user_agent=request.headers.get("user-agent"),
    )
    _set_auth_cookies(response, raw_token, session.csrf_token)

    log.info("Admin %r logged in from %s", user.username, ip_address)
    return AdminRead(
        username=user.username,
        last_login_at=user.last_login_at,
        csrf_token=session.csrf_token,
    )


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="admin_logout",
    summary="Revoke the current admin session",
)
async def logout(response: Response, db: SessionDep, session: SessionDependency):
    revoke_session(db, session)
    _clear_auth_cookies(response)
    log.info("Admin session %s logged out", session.id)


@router.get(
    "/me",
    response_model=AdminRead,
    operation_id="admin_me",
    summary="Describe the logged-in admin",
)
async def me(session: SessionDependency):
    """Also re-issues the CSRF token, so a dashboard reload can recover it
    without forcing the user to log in again."""
    return AdminRead(
        username=session.user.username,
        last_login_at=session.user.last_login_at,
        csrf_token=session.csrf_token,
    )


@router.post(
    "/sessions/revoke-all",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="admin_revoke_all_sessions",
    summary="Sign out of every session, including this one",
)
async def revoke_all(response: Response, db: SessionDep, session: SessionDependency):
    """The action that makes listing sessions useful.

    Without this, spotting an unfamiliar session left nothing to do about it. The
    current session is revoked too, deliberately: after a suspected compromise
    the safe end state is everything closed and a fresh login.
    """
    closed = revoke_all_sessions(db, session.user_id)
    _clear_auth_cookies(response)
    log.warning("Admin %r revoked all %d session(s)", session.user.username, closed)


@router.get(
    "/sessions",
    response_model=list[dict],
    operation_id="admin_list_sessions",
    summary="List the current admin's active sessions",
)
async def list_sessions(db: SessionDep, session: SessionDependency):
    """Lets you spot a session you do not recognise and log everything out."""
    rows = db.exec(
        select(AdminSession)
        .where(AdminSession.user_id == session.user_id)
        .order_by(AdminSession.created_at.desc())
    ).all()
    return [
        {
            "id": row.id,
            "current": row.id == session.id,
            "created_at": row.created_at.isoformat(),
            "last_used_at": row.last_used_at.isoformat(),
            "expires_at": row.expires_at.isoformat(),
            "revoked": row.revoked_at is not None,
            "ip_address": row.ip_address,
            "user_agent": row.user_agent,
        }
        for row in rows
    ]
