"""Trial requests, and the decision on them.

The 14-day Pro trial used to be self-serve: a customer pressed a button and had
it. It is now asked for and granted, which means there is a thing to record —
who asked, when, who decided, and what they decided.

One table, moving through one small state machine:

    pending ──approve──> approved
            └─decline──> declined

Nothing moves backwards. A declined request is not reopened, it is superseded by
a new one, because "why was I turned down in March" is a question the row has to
keep being able to answer.

**A request is not an entitlement.** Approving is what grants Pro, and it does so
through the same `grant()` the admin routes use — so a trial appears in the tier
history beside every other grant rather than in a private ledger of its own.

Deliberately not under `admin/`, even though only an admin can decide one. Both
sides of the conversation live here — a customer opens a request, an admin
closes it — and putting it in the admin package would mean the customer router
importing from `admin`, which drags the whole administrative surface into a
module that must never touch it.
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlmodel import Field, Session, SQLModel, col, select

from api.statpitch.accounts.models import StatPitchAccount, utcnow
from api.statpitch.admin.grants import grant

log = logging.getLogger("statpitch.trials")

# How long an approved trial runs. The pricing page says fourteen days.
TRIAL_DAYS = 14

TrialStatus = Literal["pending", "approved", "declined"]


class StatPitchTrialRequest(SQLModel, table=True):
    """One request for the trial, and what came of it."""

    __tablename__: str = "statpitch_trial_request"

    id: int | None = Field(default=None, primary_key=True)
    account_id: int = Field(foreign_key="statpitch_account.id", ondelete="CASCADE", index=True)

    status: str = Field(default="pending", index=True, max_length=16)
    # What the customer said when asking. Optional: a blank request is still a
    # request, and demanding a paragraph before somebody can ask for a trial is
    # a way of getting fewer trials.
    message: str | None = Field(default=None, max_length=500)
    requested_at: datetime = Field(default_factory=utcnow, index=True)

    decided_at: datetime | None = Field(default=None)
    # The admin username, or "api_key". Text rather than a foreign key into
    # `admin_user`, matching the tier grants: a decision should stay explicable
    # after the admin who made it is gone.
    decided_by: str | None = Field(default=None, max_length=64)
    # Why it was declined, mostly. Worth having on an approval too when the
    # reason was unusual.
    decision_reason: str | None = Field(default=None, max_length=200)


def pending_for(db: Session, account_id: int) -> StatPitchTrialRequest | None:
    return db.exec(
        select(StatPitchTrialRequest).where(
            StatPitchTrialRequest.account_id == account_id,
            col(StatPitchTrialRequest.status) == "pending",
        )
    ).first()


def latest_for(db: Session, account_id: int) -> StatPitchTrialRequest | None:
    """The most recent request, whatever became of it.

    What the frontend needs to decide between "Request a trial", "Awaiting
    review" and "Your request was declined".
    """
    return db.exec(
        select(StatPitchTrialRequest)
        .where(StatPitchTrialRequest.account_id == account_id)
        .order_by(
            col(StatPitchTrialRequest.requested_at).desc(), col(StatPitchTrialRequest.id).desc()
        )
    ).first()


def queue(db: Session, status: str | None = "pending") -> list[StatPitchTrialRequest]:
    """The requests waiting on somebody, oldest first.

    Oldest first because it is a queue: whoever has been waiting longest should
    be dealt with first, which is the opposite of how the account list reads.
    """
    statement = select(StatPitchTrialRequest)
    if status:
        statement = statement.where(col(StatPitchTrialRequest.status) == status)
    return db.exec(statement.order_by(col(StatPitchTrialRequest.requested_at))).all()


def open_request(
    db: Session, account: StatPitchAccount, message: str | None
) -> StatPitchTrialRequest:
    """Record a request. The caller has already checked it is allowed."""
    record = StatPitchTrialRequest(
        account_id=account.id,
        message=(message or "").strip() or None,
    )
    db.add(record)
    db.commit()
    db.refresh(record)

    log.info("Account %s requested the trial", account.id)
    return record


def approve(
    db: Session,
    request: StatPitchTrialRequest,
    account: StatPitchAccount,
    *,
    decided_by: str,
    reason: str | None = None,
) -> StatPitchTrialRequest:
    """Grant the trial and close the request.

    The grant goes through the same path an admin tier change uses, so it lands
    in the tier history rather than in a private ledger only this feature knows
    about. `trial_used_at` is stamped here and never cleared by the product —
    only an admin reset can undo it.
    """
    now = utcnow()

    grant(
        db,
        account,
        tier="pro",
        expires_at=now + timedelta(days=TRIAL_DAYS),
        reason=reason or f"{TRIAL_DAYS}-day trial approved",
        granted_by=decided_by,
        source="trial",
    )

    account.trial_used_at = now
    db.add(account)

    request.status = "approved"
    request.decided_at = datetime.now(UTC).replace(tzinfo=None)
    request.decided_by = decided_by
    request.decision_reason = (reason or "").strip() or None
    db.add(request)
    db.commit()
    db.refresh(request)

    log.info("Admin %s approved the trial for account %s", decided_by, account.id)
    return request


def decline(
    db: Session,
    request: StatPitchTrialRequest,
    *,
    decided_by: str,
    reason: str | None = None,
) -> StatPitchTrialRequest:
    """Turn a request down, without touching the account.

    Declining costs the customer nothing: `trial_used_at` stays clear, so they
    can ask again. Being told no once is not the same as having had the trial.
    """
    request.status = "declined"
    request.decided_at = utcnow()
    request.decided_by = decided_by
    request.decision_reason = (reason or "").strip() or None
    db.add(request)
    db.commit()
    db.refresh(request)

    log.info("Admin %s declined the trial for account %s", decided_by, request.account_id)
    return request
