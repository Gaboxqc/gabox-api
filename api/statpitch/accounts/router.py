"""StatPitch customer account endpoints.

Unlike the admin, these accounts *do* have a public registration route — that is
the product. What keeps it from being pure attack surface is that a row here
grants nothing except a tier, and the weakest tier is the default.
"""

import logging
from datetime import timedelta

from fastapi import APIRouter, HTTPException, Request, Response, status
from sqlmodel import select

from api.core.auth.passwords import (
    describe_password_problem,
    hash_password,
    verify_password,
    waste_time_like_a_verification,
)
from api.core.config import settings
from api.core.database import SessionDep
from api.statpitch.accounts.deps import AccountSessionDep, RequiredAccount, client_ip
from api.statpitch.accounts.keys import StatPitchApiKey, issue, list_for, revoke
from api.statpitch.accounts.models import (
    AccountRead,
    ApiKeyCreateRequest,
    ApiKeyIssued,
    ApiKeyRead,
    LoginRequest,
    PasswordChangeRequest,
    RegisterRequest,
    StatPitchAccount,
    utcnow,
)
from api.statpitch.accounts.sessions import (
    clear_failures,
    create_session,
    is_locked_out,
    prune_old_attempts,
    record_attempt,
    revoke_all_sessions,
    revoke_session,
)
from api.statpitch.tiers import Feature, allows

log = logging.getLogger("statpitch.accounts")

router = APIRouter(prefix="/accounts", tags=["StatPitch: Accounts"])

# Same body for "no such account" and "wrong password" — anything else turns
# login into a way to discover who has an account here.
_INVALID_CREDENTIALS = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Invalid credentials.",
)


def _too_many_attempts() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="Too many failed attempts. Try again later.",
        headers={"Retry-After": str(settings.statpitch_login_attempt_window_seconds)},
    )


def _set_auth_cookies(response: Response, raw_token: str, csrf_token: str) -> None:
    response.set_cookie(
        key=settings.statpitch_session_cookie_name,
        value=raw_token,
        max_age=settings.statpitch_session_max_age_seconds,
        httponly=True,  # unreadable from JavaScript, so XSS cannot exfiltrate it
        secure=settings.session_cookie_secure,
        samesite=settings.session_cookie_samesite,
        path="/",
    )
    response.set_cookie(
        key=settings.statpitch_csrf_cookie_name,
        value=csrf_token,
        max_age=settings.statpitch_session_max_age_seconds,
        # Readable, unlike the session cookie. The frontend takes the token from
        # the response body instead; this is kept for same-origin deployments
        # and as a double-submit signal.
        httponly=False,
        secure=settings.session_cookie_secure,
        samesite=settings.session_cookie_samesite,
        path="/",
    )


def _clear_auth_cookies(response: Response) -> None:
    for name in (settings.statpitch_session_cookie_name, settings.statpitch_csrf_cookie_name):
        response.delete_cookie(
            key=name,
            path="/",
            secure=settings.session_cookie_secure,
            samesite=settings.session_cookie_samesite,
        )


def _find(db: SessionDep, email: str) -> StatPitchAccount | None:
    return db.exec(select(StatPitchAccount).where(StatPitchAccount.email == email)).first()


# ==============================================================================
# REGISTRATION
# ==============================================================================


@router.post(
    "/register",
    response_model=AccountRead,
    status_code=status.HTTP_201_CREATED,
    operation_id="statpitch_register",
    summary="Create a free account and sign in",
)
async def register(
    payload: RegisterRequest,
    request: Request,
    response: Response,
    db: SessionDep,
):
    """Signing up logs you straight in — a confirm-your-email wall before the
    product has been seen is the fastest way to lose the signup."""
    ip_address = client_ip(request)

    # Registration shares the login throttle. Without it this endpoint is a free
    # way to fill the table, and an argon2 hash per request is a cheap way to
    # burn the function's CPU budget.
    if is_locked_out(db, payload.email, ip_address):
        raise _too_many_attempts()

    problem = describe_password_problem(payload.password)
    if problem:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=problem)

    if _find(db, payload.email) is not None:
        # Deliberately explicit. Hiding this would be user-enumeration theatre:
        # anyone can discover the same fact by trying to register, and a vague
        # error just leaves a real person stuck at a form that will never work.
        # The password reset flow is where enumeration actually has to be
        # guarded, and that is handled there.
        record_attempt(db, payload.email, ip_address, succeeded=False)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with that email already exists.",
        )

    account = StatPitchAccount(
        email=payload.email,
        password_hash=hash_password(payload.password),
    )
    db.add(account)
    db.commit()
    db.refresh(account)

    session, raw_token = create_session(
        db,
        account,
        ip_address=ip_address,
        user_agent=request.headers.get("user-agent"),
    )
    _set_auth_cookies(response, raw_token, session.csrf_token)

    log.info("StatPitch account %s registered from %s", account.id, ip_address)
    return AccountRead.of(account, session.csrf_token)


# ==============================================================================
# SESSION
# ==============================================================================


@router.post(
    "/login",
    response_model=AccountRead,
    operation_id="statpitch_login",
    summary="Sign in and open a session",
)
async def login(
    credentials: LoginRequest,
    request: Request,
    response: Response,
    db: SessionDep,
):
    ip_address = client_ip(request)

    if is_locked_out(db, credentials.email, ip_address):
        log.warning("Locked-out StatPitch login for %r from %s", credentials.email, ip_address)
        raise _too_many_attempts()

    account = _find(db, credentials.email)

    if account is None:
        # Burn the same CPU a real verification would, then fail identically.
        waste_time_like_a_verification()
        record_attempt(db, credentials.email, ip_address, succeeded=False)
        raise _INVALID_CREDENTIALS

    matched, rehashed = verify_password(account.password_hash, credentials.password)

    # The active flag is checked after the password, so that disabling an
    # account does not make it answer a wrong password differently.
    if not matched or not account.is_active:
        record_attempt(db, credentials.email, ip_address, succeeded=False)
        raise _INVALID_CREDENTIALS

    if rehashed:
        account.password_hash = rehashed
        db.add(account)
        db.commit()

    record_attempt(db, credentials.email, ip_address, succeeded=True)
    clear_failures(db, credentials.email, ip_address)
    prune_old_attempts(db)

    session, raw_token = create_session(
        db,
        account,
        ip_address=ip_address,
        user_agent=request.headers.get("user-agent"),
    )
    _set_auth_cookies(response, raw_token, session.csrf_token)

    return AccountRead.of(account, session.csrf_token)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="statpitch_logout",
    summary="Revoke the current session",
)
async def logout(response: Response, db: SessionDep, session: AccountSessionDep):
    revoke_session(db, session)
    _clear_auth_cookies(response)


@router.get(
    "/me",
    response_model=AccountRead,
    operation_id="statpitch_me",
    summary="Describe the signed-in account and its tier",
)
async def me(session: AccountSessionDep):
    """The frontend's source of truth for what to show.

    Re-issuing the CSRF token here is what lets a page reload recover it without
    a fresh login, and re-reading the tier is what makes an expiry take effect
    without anyone being logged out.
    """
    return AccountRead.of(session.account, session.csrf_token)


# ==============================================================================
# PASSWORD
# ==============================================================================


@router.post(
    "/password",
    response_model=AccountRead,
    operation_id="statpitch_change_password",
    summary="Change the password and close every other session",
)
async def change_password(
    payload: PasswordChangeRequest,
    request: Request,
    response: Response,
    db: SessionDep,
    session: AccountSessionDep,
):
    account = session.account

    matched, _ = verify_password(account.password_hash, payload.current_password)
    if not matched:
        raise _INVALID_CREDENTIALS

    problem = describe_password_problem(payload.new_password)
    if problem:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=problem)

    account.password_hash = hash_password(payload.new_password)
    db.add(account)
    db.commit()

    # Everything is revoked, including this session, and then a fresh one is
    # opened. Changing a password after a suspected compromise has to lock the
    # attacker out; leaving the current session alive and revoking the rest
    # would be the same thing done less simply.
    revoke_all_sessions(db, account.id)

    new_session, raw_token = create_session(
        db,
        account,
        ip_address=client_ip(request),
        user_agent=request.headers.get("user-agent"),
    )
    _set_auth_cookies(response, raw_token, new_session.csrf_token)

    log.info("StatPitch account %s changed password", account.id)
    return AccountRead.of(account, new_session.csrf_token)


@router.post(
    "/sessions/revoke-all",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="statpitch_revoke_all_sessions",
    summary="Sign out everywhere, including here",
)
async def revoke_all(response: Response, db: SessionDep, account: RequiredAccount):
    closed = revoke_all_sessions(db, account.id)
    _clear_auth_cookies(response)
    log.info("StatPitch account %s revoked %d session(s)", account.id, closed)


# ==============================================================================
# TRIAL
# ==============================================================================


@router.post(
    "/trial",
    response_model=AccountRead,
    operation_id="statpitch_start_trial",
    summary="Start the 14-day Pro trial",
)
async def start_trial(db: SessionDep, session: AccountSessionDep):
    """No card, no billing provider — a trial is just a Pro tier with an end
    date, which `effective_tier` already knows how to let lapse.

    `trial_used_at` is what makes it once-only, and it is never cleared: a
    second trial has to be a deliberate manual grant, not a side effect of
    cancelling.
    """
    account = session.account

    if account.trial_used_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The trial has already been used on this account.",
        )
    if account.effective_tier != "free":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This account already has a paid tier.",
        )

    now = utcnow()
    account.tier = "pro"
    account.tier_source = "trial"
    account.tier_expires_at = now + timedelta(days=14)
    account.tier_updated_at = now
    account.trial_used_at = now
    db.add(account)
    db.commit()
    db.refresh(account)

    log.info("StatPitch account %s started the Pro trial", account.id)
    return AccountRead.of(account, session.csrf_token)


# ==============================================================================
# API KEYS
# ==============================================================================


def _as_read(key: StatPitchApiKey) -> ApiKeyRead:
    return ApiKeyRead(
        id=key.id,
        name=key.name,
        prefix=key.prefix,
        created_at=key.created_at,
        last_used_at=key.last_used_at,
        revoked=key.revoked_at is not None,
    )


def _require_api_access(account) -> None:
    if not allows(account.effective_tier, Feature.API_ACCESS):
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="API access is an Elite feature.",
        )


@router.post(
    "/keys",
    response_model=ApiKeyIssued,
    status_code=status.HTTP_201_CREATED,
    operation_id="statpitch_create_api_key",
    summary="Issue an API key (Elite)",
)
async def create_api_key(
    payload: ApiKeyCreateRequest,
    db: SessionDep,
    session: AccountSessionDep,
):
    """The only response that ever contains the key itself.

    Nothing stores the raw value, so a lost key is replaced rather than
    recovered — which is exactly the property that makes storing only its hash
    worth anything.
    """
    account = session.account
    _require_api_access(account)

    key, raw_key = issue(db, account.id, payload.name.strip())
    return ApiKeyIssued(**_as_read(key).model_dump(), key=raw_key)


@router.get(
    "/keys",
    response_model=list[ApiKeyRead],
    operation_id="statpitch_list_api_keys",
    summary="List this account's API keys",
)
async def list_api_keys(db: SessionDep, session: AccountSessionDep):
    """Revoked keys are listed too. A key that turns up in a log later is worth
    being able to identify, which deleting the row would prevent."""
    return [_as_read(key) for key in list_for(db, session.account_id)]


@router.delete(
    "/keys/{key_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="statpitch_revoke_api_key",
    summary="Revoke an API key",
)
async def revoke_api_key(key_id: int, db: SessionDep, session: AccountSessionDep):
    key = db.get(StatPitchApiKey, key_id)
    # Somebody else's key is reported as absent, not as forbidden: 404 does not
    # confirm that the id exists.
    if key is None or key.account_id != session.account_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No such API key.",
        )

    # Revoking is not gated on the tier. An account that lapses from Elite must
    # still be able to turn off keys it issued while it had them.
    if key.revoked_at is None:
        revoke(db, key)
