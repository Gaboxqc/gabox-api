"""Administering StatPitch customer accounts.

Guarded by `api.core.auth.require_admin`, which is imported rather than
reimplemented: it already accepts either the dashboard session or the master
key, already fails closed, and already hands back a `Principal` naming whoever
is acting. A second admin check living in this module would be a second thing
to get wrong.

That is also why these routes sit here and not under `/statpitch/accounts`:
everything under that prefix authenticates as a *customer*, and a customer must
never reach any of this. The prefixes make the boundary visible in the URL, and
the dependency makes it real.

Writes are recorded by the existing `AuditMiddleware` — method, path, status and
which credential authorised it — so nothing here has to log its own trail.
"""

import logging
import secrets

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlmodel import col, func, select

from api.core.auth.deps import AdminDep
from api.core.auth.passwords import hash_password
from api.core.database import SessionDep
from api.core.deps import PageDep
from api.statpitch.accounts.keys import StatPitchApiKey
from api.statpitch.accounts.keys import list_for as list_keys_for
from api.statpitch.accounts.keys import revoke as revoke_key
from api.statpitch.accounts.models import (
    AdminAccountCreated,
    AdminAccountCreateRequest,
    AdminAccountRead,
    AdminAccountUpdateRequest,
    AdminSessionRead,
    ApiKeyRead,
    StatPitchAccount,
    StatPitchAccountSession,
    TierGrantRead,
    TierGrantRequest,
    utcnow,
)
from api.statpitch.accounts.sessions import revoke_all_sessions
from api.statpitch.admin.grants import as_naive_utc, grant, history

log = logging.getLogger("statpitch.admin")

router = APIRouter(prefix="/admin", tags=["StatPitch: Administration"])

# Long enough that it is never guessed, and shown once. An admin sends it to the
# account holder out of band; they replace it at /statpitch/accounts/password.
_TEMPORARY_PASSWORD_BYTES = 18


def _account_or_404(db: SessionDep, account_id: int) -> StatPitchAccount:
    account = db.get(StatPitchAccount, account_id)
    if account is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No StatPitch account with id {account_id}.",
        )
    return account


def _counts(db: SessionDep, account_ids: list[int]) -> dict[int, tuple[int, int]]:
    """Live sessions and live API keys per account, in two queries.

    Counted in aggregate rather than per row: a page of accounts would otherwise
    cost two round trips each, which is the shape of query that looks fine on a
    dozen customers and stops looking fine on a thousand.
    """
    if not account_ids:
        return {}

    now = utcnow()
    sessions = dict(
        db.exec(
            select(StatPitchAccountSession.account_id, func.count())
            .where(
                col(StatPitchAccountSession.account_id).in_(account_ids),
                col(StatPitchAccountSession.revoked_at).is_(None),
                StatPitchAccountSession.expires_at > now,
            )
            .group_by(col(StatPitchAccountSession.account_id))
        ).all()
    )
    keys = dict(
        db.exec(
            select(StatPitchApiKey.account_id, func.count())
            .where(
                col(StatPitchApiKey.account_id).in_(account_ids),
                col(StatPitchApiKey.revoked_at).is_(None),
            )
            .group_by(col(StatPitchApiKey.account_id))
        ).all()
    )
    return {
        identifier: (int(sessions.get(identifier, 0)), int(keys.get(identifier, 0)))
        for identifier in account_ids
    }


def _read(db: SessionDep, account: StatPitchAccount) -> AdminAccountRead:
    active_sessions, live_keys = _counts(db, [account.id]).get(account.id, (0, 0))
    return AdminAccountRead.of(account, active_sessions=active_sessions, live_api_keys=live_keys)


# ==============================================================================
# BROWSING
# ==============================================================================


@router.get(
    "/accounts",
    response_model=list[AdminAccountRead],
    operation_id="statpitch_admin_list_accounts",
    summary="List customer accounts",
)
async def list_accounts(
    db: SessionDep,
    response: Response,
    page: PageDep,
    _: AdminDep,
    tier: str | None = Query(default=None, description="Filter on the granted tier"),
    email: str | None = Query(default=None, description="Case-insensitive substring"),
    is_active: bool | None = Query(default=None),
):
    """Newest first, because the account you want is usually the recent one."""
    query = select(StatPitchAccount)

    if tier:
        query = query.where(col(StatPitchAccount.tier) == tier)
    if email:
        query = query.where(col(StatPitchAccount.email).contains(email.strip().lower()))
    if is_active is not None:
        query = query.where(col(StatPitchAccount.is_active) == is_active)

    total = db.exec(select(func.count()).select_from(query.subquery())).one()
    response.headers["X-Total-Count"] = str(int(total))

    accounts = db.exec(
        query.order_by(col(StatPitchAccount.created_at).desc())
        .offset(page.offset)
        .limit(page.limit)
    ).all()

    counts = _counts(db, [account.id for account in accounts])
    return [
        AdminAccountRead.of(
            account,
            active_sessions=counts.get(account.id, (0, 0))[0],
            live_api_keys=counts.get(account.id, (0, 0))[1],
        )
        for account in accounts
    ]


@router.get(
    "/accounts/{account_id}",
    response_model=AdminAccountRead,
    operation_id="statpitch_admin_get_account",
    summary="One customer account",
)
async def get_account(account_id: int, db: SessionDep, _: AdminDep):
    return _read(db, _account_or_404(db, account_id))


# ==============================================================================
# CREATING
# ==============================================================================


@router.post(
    "/accounts",
    response_model=AdminAccountCreated,
    status_code=status.HTTP_201_CREATED,
    operation_id="statpitch_admin_create_account",
    summary="Create an account and return its one-time password",
)
async def create_account(
    payload: AdminAccountCreateRequest,
    db: SessionDep,
    principal: AdminDep,
):
    """Creates a free account with a generated password, returned once.

    The request carries no password field. An admin typing somebody else's
    password would put a plaintext credential through a form, a request body and
    almost certainly a log; generating one keeps it out of all three, and the
    account holder replaces it at `/statpitch/accounts/password`.

    Until an email provider exists the password has to travel to them by hand,
    which is a reason to prefer letting people sign up themselves.
    """
    existing = db.exec(
        select(StatPitchAccount).where(StatPitchAccount.email == payload.email)
    ).first()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with that email already exists.",
        )

    temporary_password = secrets.token_urlsafe(_TEMPORARY_PASSWORD_BYTES)
    account = StatPitchAccount(
        email=payload.email,
        password_hash=hash_password(temporary_password),
    )
    db.add(account)
    db.commit()
    db.refresh(account)

    log.info("Admin %s created StatPitch account %s", principal.username, account.id)
    return AdminAccountCreated(
        **_read(db, account).model_dump(), temporary_password=temporary_password
    )


# ==============================================================================
# CHANGING AND REMOVING
# ==============================================================================


@router.patch(
    "/accounts/{account_id}",
    response_model=AdminAccountRead,
    operation_id="statpitch_admin_update_account",
    summary="Activate or deactivate an account",
)
async def update_account(
    account_id: int,
    payload: AdminAccountUpdateRequest,
    db: SessionDep,
    principal: AdminDep,
):
    """Deactivating closes every live session as well as barring login.

    Leaving them open would mean a disabled account carried on reading for up to
    thirty days, since a session is only checked against `is_active` when it is
    loaded — which is exactly the window that makes "disabled" feel like it did
    not work.
    """
    account = _account_or_404(db, account_id)
    account.is_active = payload.is_active
    db.add(account)
    db.commit()

    if not payload.is_active:
        closed = revoke_all_sessions(db, account.id)
        log.warning(
            "Admin %s deactivated StatPitch account %s and closed %d session(s)",
            principal.username,
            account.id,
            closed,
        )

    db.refresh(account)
    return _read(db, account)


@router.delete(
    "/accounts/{account_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="statpitch_admin_delete_account",
    summary="Delete an account and everything belonging to it",
)
async def delete_account(account_id: int, db: SessionDep, principal: AdminDep):
    """Irreversible, and it takes the account's sessions, API keys and unlock
    history with it.

    Prefer deactivating. That stops login just as immediately, keeps the history
    a support conversation usually needs, and can be undone.
    """
    account = _account_or_404(db, account_id)
    email = account.email

    db.delete(account)
    db.commit()

    log.warning("Admin %s deleted StatPitch account %s (%s)", principal.username, account_id, email)


# ==============================================================================
# TIERS
# ==============================================================================


@router.patch(
    "/accounts/{account_id}/tier",
    response_model=AdminAccountRead,
    operation_id="statpitch_admin_set_tier",
    summary="Grant, extend or revoke a tier",
)
async def set_tier(
    account_id: int,
    payload: TierGrantRequest,
    db: SessionDep,
    principal: AdminDep,
):
    """Moves the account and writes a row into its history.

    Granting the same tier again is an extension, not a mistake — it is how a
    renewal is recorded, and it leaves its own entry.

    An expiry already in the past is refused. It would technically work, in the
    sense that `effective_tier` would read `free` immediately, but nobody means
    that: it is a typo in a date, and honouring it silently would look like the
    grant simply failed.
    """
    account = _account_or_404(db, account_id)

    expires_at = as_naive_utc(payload.expires_at)
    if expires_at is not None and expires_at <= utcnow():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="expires_at is already in the past; the grant would lapse immediately.",
        )

    grant(
        db,
        account,
        tier=payload.tier,
        expires_at=expires_at,
        reason=payload.reason,
        granted_by=principal.username or "api_key",
    )
    return _read(db, account)


@router.get(
    "/accounts/{account_id}/grants",
    response_model=list[TierGrantRead],
    operation_id="statpitch_admin_tier_history",
    summary="Every tier this account has been given, newest first",
)
async def tier_history(account_id: int, db: SessionDep, _: AdminDep):
    """What the account row cannot tell you: how it got here."""
    _account_or_404(db, account_id)
    return history(db, account_id)


@router.post(
    "/accounts/{account_id}/trial/reset",
    response_model=AdminAccountRead,
    operation_id="statpitch_admin_reset_trial",
    summary="Let an account take the 14-day trial again",
)
async def reset_trial(account_id: int, db: SessionDep, principal: AdminDep):
    """Clears `trial_used_at`, and nothing else.

    Deliberately not a tier change: it does not grant Pro, it restores the
    ability to *start* the trial from the product. Somebody whose trial was cut
    short by a broken deploy wants this; somebody asking for a second free month
    wants a grant, which is a different button and leaves a different trail.
    """
    account = _account_or_404(db, account_id)

    account.trial_used_at = None
    db.add(account)
    db.commit()
    db.refresh(account)

    log.info("Admin %s reset the trial on account %s", principal.username, account.id)
    return _read(db, account)


# ==============================================================================
# SESSIONS AND KEYS
# ==============================================================================


@router.get(
    "/accounts/{account_id}/sessions",
    response_model=list[AdminSessionRead],
    operation_id="statpitch_admin_list_sessions",
    summary="An account's sessions, newest first",
)
async def list_account_sessions(account_id: int, db: SessionDep, _: AdminDep):
    """Revoked and expired sessions are listed too.

    "Somebody was signed in from an address I do not recognise" is answered by
    the history, not by what happens to still be live — so hiding the closed
    ones would hide the thing being asked about.
    """
    _account_or_404(db, account_id)
    now = utcnow()

    rows = db.exec(
        select(StatPitchAccountSession)
        .where(StatPitchAccountSession.account_id == account_id)
        .order_by(col(StatPitchAccountSession.created_at).desc())
    ).all()

    return [
        AdminSessionRead(
            id=row.id,
            created_at=row.created_at,
            last_used_at=row.last_used_at,
            expires_at=row.expires_at,
            revoked=row.revoked_at is not None,
            live=row.revoked_at is None and row.expires_at > now,
            ip_address=row.ip_address,
            user_agent=row.user_agent,
        )
        for row in rows
    ]


@router.post(
    "/accounts/{account_id}/sessions/revoke-all",
    response_model=AdminAccountRead,
    operation_id="statpitch_admin_revoke_sessions",
    summary="Sign an account out everywhere",
)
async def revoke_account_sessions(account_id: int, db: SessionDep, principal: AdminDep):
    """For the call that starts "I think somebody else is using my account".

    Leaves the account active — it stops the sessions, not the person. Barring
    them entirely is `PATCH .../{id}` with `is_active: false`.
    """
    account = _account_or_404(db, account_id)
    closed = revoke_all_sessions(db, account.id)

    log.warning(
        "Admin %s revoked %d session(s) on account %s", principal.username, closed, account.id
    )
    db.refresh(account)
    return _read(db, account)


@router.get(
    "/accounts/{account_id}/keys",
    response_model=list[ApiKeyRead],
    operation_id="statpitch_admin_list_keys",
    summary="An account's API keys",
)
async def list_account_keys(account_id: int, db: SessionDep, _: AdminDep):
    """Never the key itself — only its hash was ever stored."""
    _account_or_404(db, account_id)
    return [
        ApiKeyRead(
            id=key.id,
            name=key.name,
            prefix=key.prefix,
            created_at=key.created_at,
            last_used_at=key.last_used_at,
            revoked=key.revoked_at is not None,
        )
        for key in list_keys_for(db, account_id)
    ]


@router.delete(
    "/keys/{key_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="statpitch_admin_revoke_key",
    summary="Revoke an API key on a customer's behalf",
)
async def revoke_account_key(key_id: int, db: SessionDep, principal: AdminDep):
    """Keyed on the key rather than nested under its account: a leaked key is
    reported by its prefix, and looking up who owns it first is a step that
    matters only to the person who is already holding the thing.

    Revoked, never deleted, so a key that turns up in a log later is still
    identifiable.
    """
    key = db.get(StatPitchApiKey, key_id)
    if key is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No API key with id {key_id}.",
        )

    if key.revoked_at is None:
        revoke_key(db, key)
        log.warning("Admin %s revoked API key %s", principal.username, key.prefix)
