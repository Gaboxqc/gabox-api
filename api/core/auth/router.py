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

    `X-Forwarded-For` is client-controllable in general, so this is only safe
    because the app is always behind a proxy that overwrites it. Directly
    exposed, an attacker could spoof it to dodge the lockout.
    """
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
        # Readable on purpose: the dashboard copies it into the X-CSRF-Token
        # header. It is not a credential on its own — the session cookie is.
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
    return AdminRead(username=user.username, last_login_at=user.last_login_at)


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
    return AdminRead(
        username=session.user.username,
        last_login_at=session.user.last_login_at,
    )


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
