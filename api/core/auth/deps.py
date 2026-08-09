"""Route dependencies for admin-protected endpoints.

`require_admin` accepts either an admin session cookie (the dashboard) or the
existing `X-API-KEY` master key (scripts, StatPitch). Keeping both means nothing
that works today breaks, while the browser never needs the master key.
"""

import secrets
from dataclasses import dataclass
from typing import Annotated

from fastapi import Cookie, Depends, Header, HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader

from api.core.auth.models import AdminSession
from api.core.auth.sessions import load_valid_session, touch_session
from api.core.config import settings
from api.core.database import SessionDep

CSRF_HEADER_NAME = "X-CSRF-Token"

# Methods that cannot change state, so they need no CSRF token.
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

# auto_error=False for the same reason as api.core.security: a missing header
# must produce the same 401 as a wrong one.
_api_key_header = APIKeyHeader(name="X-API-KEY", auto_error=False)

_UNAUTHENTICATED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    # One message for every failure mode. Saying "no session" versus "bad key"
    # would tell an attacker which door they are standing at.
    detail="Authentication required.",
    headers={"WWW-Authenticate": "Cookie"},
)

_CSRF_FAILED = HTTPException(
    status_code=status.HTTP_403_FORBIDDEN,
    detail="Missing or invalid CSRF token.",
)


@dataclass(frozen=True)
class Principal:
    """Who is making the request, and how they proved it."""

    kind: str  # "session" | "api_key"
    username: str | None = None
    session_id: int | None = None


def _api_key_matches(api_key: str | None) -> bool:
    if api_key is None:
        return False
    return secrets.compare_digest(api_key.encode("utf-8"), settings.api_master_key.encode("utf-8"))


def _csrf_ok(request: Request, session: AdminSession, csrf_header: str | None) -> bool:
    """Double-submit check, verified against the value held server side.

    SameSite=Lax already blocks a cross-site form POST from carrying the session
    cookie. This is the second layer, and it compares the header against the
    session row rather than against another cookie, so an attacker who can only
    write cookies still cannot pass it.
    """
    if request.method.upper() in _SAFE_METHODS:
        return True
    if csrf_header is None:
        return False
    return secrets.compare_digest(csrf_header.encode("utf-8"), session.csrf_token.encode("utf-8"))


def _enforce_csrf(request: Request, session: AdminSession, csrf_header: str | None) -> None:
    if not _csrf_ok(request, session, csrf_header):
        raise _CSRF_FAILED


async def require_admin(
    request: Request,
    db: SessionDep,
    api_key: Annotated[str | None, Security(_api_key_header)] = None,
    session_token: Annotated[str | None, Cookie(alias=settings.session_cookie_name)] = None,
    csrf_header: Annotated[str | None, Header(alias=CSRF_HEADER_NAME)] = None,
) -> Principal:
    """Authenticate an admin caller, or raise 401.

    The session is tried first so that requests are attributed to a person
    rather than to the shared key whenever possible. A cookie that is expired,
    revoked, or missing its CSRF token does not short-circuit: the master key is
    still consulted, so a script that happens to carry a stale cookie keeps
    working. Only when neither credential holds does this fail.
    """
    csrf_rejected = False

    if session_token:
        session = load_valid_session(db, session_token)
        if session is not None and session.user.is_active:
            if _csrf_ok(request, session, csrf_header):
                touch_session(db, session)
                principal = Principal(
                    kind="session",
                    username=session.user.username,
                    session_id=session.id,
                )
                # Middleware runs after the handler and cannot resolve
                # dependencies, so the principal is handed over on the request.
                request.state.principal = principal
                return principal
            csrf_rejected = True

    # The master key is exempt from CSRF: browsers never attach a custom header
    # on their own, and a cross-site caller cannot add one without passing a
    # CORS preflight this app's origin allowlist rejects.
    if _api_key_matches(api_key):
        principal = Principal(kind="api_key")
        request.state.principal = principal
        return principal

    # A valid session with a bad token is a CSRF failure (403), not a missing
    # credential (401) — reporting 401 would send the dashboard to the login page
    # when the real fault is a stale token it should simply refresh.
    if csrf_rejected:
        raise _CSRF_FAILED

    raise _UNAUTHENTICATED


AdminDep = Annotated[Principal, Depends(require_admin)]


async def require_session(
    request: Request,
    db: SessionDep,
    session_token: Annotated[str | None, Cookie(alias=settings.session_cookie_name)] = None,
    csrf_header: Annotated[str | None, Header(alias=CSRF_HEADER_NAME)] = None,
) -> AdminSession:
    """Require a real logged-in session, not the master key.

    Used by the `/auth` routes themselves: "who am I" and "log me out" are
    meaningless for a shared machine key.
    """
    if not session_token:
        raise _UNAUTHENTICATED

    session = load_valid_session(db, session_token)
    if session is None or not session.user.is_active:
        raise _UNAUTHENTICATED

    _enforce_csrf(request, session, csrf_header)
    return session


SessionDependency = Annotated[AdminSession, Depends(require_session)]
