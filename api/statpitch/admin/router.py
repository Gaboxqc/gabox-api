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
from api.statpitch.accounts.models import (
    AdminAccountCreated,
    AdminAccountCreateRequest,
    AdminAccountRead,
    AdminAccountUpdateRequest,
    StatPitchAccount,
    StatPitchAccountSession,
    utcnow,
)
from api.statpitch.accounts.sessions import revoke_all_sessions

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
