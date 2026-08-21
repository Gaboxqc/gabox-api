"""Route dependencies for StatPitch customer endpoints.

Two dependencies, and the difference between them matters:

`optional_account` is what the fixture routes use. An anonymous visitor is not
an error here — they are a free-tier reader — so it returns `None` rather than
raising, and the caller resolves that to the free tier.

`require_account` is for the routes that are meaningless without a person behind
them: "who am I", "log me out", "change my password".

Neither can authenticate an admin, and neither accepts the master key: a machine
key is nobody, so `/accounts/me` refuses it.

`current_tier` is the exception, and the distinction is deliberate. Identity and
entitlement are different questions — the master key has no account but does
have full entitlement, because it is the owner's key and the admin dashboard
carries it.
"""

import secrets
from typing import Annotated

from fastapi import Cookie, Depends, Header, HTTPException, Request, Security, status
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer

from api.core.config import settings
from api.core.database import SessionDep
from api.statpitch.accounts.keys import load_live_key, touch
from api.statpitch.accounts.models import StatPitchAccount, StatPitchAccountSession, Tier
from api.statpitch.accounts.sessions import load_valid_session, touch_session
from api.statpitch.tiers import Feature, allows

CSRF_HEADER_NAME = "X-CSRF-Token"

# auto_error=False so a missing key is simply an anonymous caller rather
# than a 403 — these routes are readable without any credential at all.
_api_key_header = APIKeyHeader(name="X-API-KEY", auto_error=False)

# Customer keys travel as `Authorization: Bearer sp_live_...`, kept off
# X-API-KEY so a customer credential can never be mistaken for the master one.
_bearer = HTTPBearer(auto_error=False, scheme_name="StatPitch API key")

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

# A key that does not resolve. Distinct from anonymity: presenting a bad
# credential is an error, whereas presenting none is simply the free tier.
_BAD_KEY = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Invalid or revoked API key.",
    headers={"WWW-Authenticate": "Bearer"},
)

_API_ACCESS_REQUIRED = HTTPException(
    status_code=status.HTTP_402_PAYMENT_REQUIRED,
    detail="API access is an Elite feature.",
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
    """Resolve a signed-in account to a tier. Anonymous reads as free.

    The single place that answers "what may this caller see", so that no route
    has to remember that `None` means free rather than means blocked.
    """
    return account.effective_tier if account is not None else "free"


async def account_from_key(
    db: SessionDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)] = None,
) -> StatPitchAccount | None:
    """The account behind a customer API key, or None if none was presented.

    Raises rather than returning None when a key *is* presented and does not
    work: silently falling back to the free tier would leave an integration
    quietly reading teasers with no idea its key had been revoked.
    """
    if credentials is None or not credentials.credentials:
        return None

    key = load_live_key(db, credentials.credentials)
    if key is None:
        raise _BAD_KEY

    account = db.get(StatPitchAccount, key.account_id)
    if account is None or not account.is_active:
        raise _BAD_KEY

    # API access is itself the Elite line. A lapsed subscription stops the key
    # working rather than quietly demoting it to free data — otherwise "API
    # access" would not be gated at all.
    if not allows(account.effective_tier, Feature.API_ACCESS):
        raise _API_ACCESS_REQUIRED

    touch(db, key)
    return account


async def current_tier(
    account: Annotated[StatPitchAccount | None, Depends(optional_account)],
    key_account: Annotated[StatPitchAccount | None, Depends(account_from_key)] = None,
    api_key: Annotated[str | None, Security(_api_key_header)] = None,
) -> Tier:
    """What the caller may see, whichever credential they brought.

    Three ways in, in descending authority:

    1. The **master key**, which has no *account* — `/accounts/me` still refuses
       it, because a machine key is nobody. It does have full *entitlement*: it
       is the owner's key, the admin dashboard carries it, and denying Gabriel
       his own ledger because he is not a paying subscriber would be absurd.
    2. A **customer API key**, which resolves to its owner's tier.
    3. A **session cookie**, or nothing at all, which is the free tier.

    Identity and entitlement are separate questions, and this is the one place
    they are answered differently.
    """
    if api_key and secrets.compare_digest(
        api_key.encode("utf-8"), settings.api_master_key.encode("utf-8")
    ):
        return "elite"
    if key_account is not None:
        return key_account.effective_tier
    return tier_of(account)


CurrentAccount = Annotated[StatPitchAccount | None, Depends(optional_account)]
RequiredAccount = Annotated[StatPitchAccount, Depends(require_account)]
AccountSessionDep = Annotated[StatPitchAccountSession, Depends(require_account_session)]
# What read routes should depend on: the tier, not the account.
CallerTier = Annotated[Tier, Depends(current_tier)]
