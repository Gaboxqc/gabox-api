"""Route dependencies for StatPitch customer endpoints.

Two dependencies, and the difference between them matters:

`optional_account` is what the fixture routes use. An anonymous visitor is not
an error here — they are a free-tier reader — so it returns `None` rather than
raising, and the caller resolves that to the free tier.

`require_account` is for the routes that are meaningless without a person behind
them: "who am I", "log me out", "change my password".

Neither of these can authenticate an admin, and the master key is deliberately
not accepted: a machine key has no tier, and quietly treating it as Elite would
make the entitlement rules untestable from the outside.
"""

import secrets
from typing import Annotated

from fastapi import Cookie, Depends, Header, HTTPException, Request, status

from api.core.config import settings
from api.core.database import SessionDep
from api.statpitch.accounts.models import StatPitchAccount, StatPitchAccountSession, Tier
from api.statpitch.accounts.sessions import load_valid_session, touch_session

CSRF_HEADER_NAME = "X-CSRF-Token"

# Methods that cannot change state, so they need no CSRF token.
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

_UNAUTHENTICATED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Sign in to continue.",
    headers={"WWW-Authenticate": "Cookie"},
)

_CSRF_FAILED = HTTPException(
    status_code=status.HTTP_403_FORBIDDEN,
    detail="Missing or invalid CSRF token.",
)


def client_ip(request: Request) -> str:
    """Caller's address, preferring the proxy header Vercel sets.

    `X-Forwarded-For` is client-controllable, so it is only believed when
    `trust_proxy_headers` says a proxy is overwriting it. Exposed directly, an
    attacker could otherwise forge the header and sidestep the login lockout by
    presenting a fresh address on every attempt.
    """
    if settings.trust_proxy_headers:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _csrf_ok(request: Request, session: StatPitchAccountSession, csrf_header: str | None) -> bool:
    """Double-submit check, verified against the value held server side.

    SameSite=Lax already blocks a cross-site form POST from carrying the cookie.
    This is the second layer, and it compares the header against the session row
    rather than against another cookie, so an attacker who can only write
    cookies still cannot pass it.
    """
    if request.method.upper() in _SAFE_METHODS:
        return True
    if csrf_header is None:
        return False
    return secrets.compare_digest(csrf_header.encode("utf-8"), session.csrf_token.encode("utf-8"))


async def _resolve_session(
    request: Request,
    db: SessionDep,
    session_token: str | None,
    csrf_header: str | None,
) -> StatPitchAccountSession | None:
    if not session_token:
        return None

    session = load_valid_session(db, session_token)
    if session is None or not session.account.is_active:
        return None

    # A bad CSRF token on a live session is a rejection, never a silent
    # downgrade to anonymous — otherwise a state-changing request would go
    # through as though nobody were signed in.
    if not _csrf_ok(request, session, csrf_header):
        raise _CSRF_FAILED

    touch_session(db, session)
    return session


async def optional_account(
    request: Request,
    db: SessionDep,
    session_token: Annotated[
        str | None, Cookie(alias=settings.statpitch_session_cookie_name)
    ] = None,
    csrf_header: Annotated[str | None, Header(alias=CSRF_HEADER_NAME)] = None,
) -> StatPitchAccount | None:
    """The signed-in customer, or None for an anonymous visitor."""
    session = await _resolve_session(request, db, session_token, csrf_header)
    return session.account if session else None


async def require_account_session(
    request: Request,
    db: SessionDep,
    session_token: Annotated[
        str | None, Cookie(alias=settings.statpitch_session_cookie_name)
    ] = None,
    csrf_header: Annotated[str | None, Header(alias=CSRF_HEADER_NAME)] = None,
) -> StatPitchAccountSession:
    """The live session row, for routes that need to revoke or list it."""
    session = await _resolve_session(request, db, session_token, csrf_header)
    if session is None:
        raise _UNAUTHENTICATED
    return session


async def require_account(
    session: Annotated[StatPitchAccountSession, Depends(require_account_session)],
) -> StatPitchAccount:
    return session.account


def tier_of(account: StatPitchAccount | None) -> Tier:
    """Resolve a caller to a tier. Anonymous reads as free.

    The single place that answers "what may this caller see", so that no route
    has to remember that `None` means free rather than means blocked.
    """
    return account.effective_tier if account is not None else "free"


CurrentAccount = Annotated[StatPitchAccount | None, Depends(optional_account)]
RequiredAccount = Annotated[StatPitchAccount, Depends(require_account)]
AccountSessionDep = Annotated[StatPitchAccountSession, Depends(require_account_session)]
