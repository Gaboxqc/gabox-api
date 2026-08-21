"""The record of who was given what, and why.

`statpitch_account.tier` is current state — one row, always the truth about
today. This is the event log behind it: append-only, never updated, one row per
grant.

That is not the duplication the fixtures had. A copied crest URL was a second
answer to a question that already had one; this answers a different question
entirely — *how did this account come to be Elite?* — which the account row
cannot answer at all, because it only remembers the latest value.

It earns its place the first time somebody emails asking why their subscription
ended, or you need to know whether the Elite you granted in March was a trial
extension or a paid year.
"""

import logging
from datetime import UTC, datetime

from sqlmodel import Field, Session, SQLModel, col, select

from api.statpitch.accounts.models import StatPitchAccount, Tier, utcnow

log = logging.getLogger("statpitch.grants")


class StatPitchTierGrant(SQLModel, table=True):
    """One tier change. Written once and never touched again."""

    __tablename__: str = "statpitch_tier_grant"

    id: int | None = Field(default=None, primary_key=True)
    account_id: int = Field(foreign_key="statpitch_account.id", ondelete="CASCADE", index=True)

    # Both sides of the change, so the history reads without having to replay
    # every earlier row to work out where it started.
    from_tier: str = Field(max_length=16)
    to_tier: str = Field(max_length=16)
    expires_at: datetime | None = Field(default=None)

    # Required, and the reason this table is worth having. A grant with no
    # explanation is the one you cannot make sense of six months later.
    reason: str = Field(max_length=200)
    # The admin username, or "api_key" when the master key was used. Text rather
    # than a foreign key into `admin_user`: this module does not depend on that
    # table, and a grant should stay explicable after the admin who made it is
    # gone.
    granted_by: str = Field(max_length=64)
    granted_at: datetime = Field(default_factory=utcnow, index=True)


def as_naive_utc(moment: datetime | None) -> datetime | None:
    """Fold an incoming timestamp into the naive UTC everything here stores.

    A JSON body may carry an offset (`2026-09-01T00:00:00-06:00`) and pydantic
    will hand back a tz-aware value. Comparing that against a naive column
    raises TypeError, so the conversion has to happen once, on the way in,
    rather than being discovered at the first expiry check.
    """
    if moment is None:
        return None
    if moment.tzinfo is None:
        return moment
    return moment.astimezone(UTC).replace(tzinfo=None)


def grant(
    db: Session,
    account: StatPitchAccount,
    *,
    tier: Tier,
    expires_at: datetime | None,
    reason: str,
    granted_by: str,
) -> StatPitchTierGrant:
    """Move an account to `tier` and record why.

    The account row and the history row are written together: a tier that
    changed without leaving a trace is exactly the state this module exists to
    prevent.
    """
    expires_at = as_naive_utc(expires_at)
    now = utcnow()

    record = StatPitchTierGrant(
        account_id=account.id,
        from_tier=account.tier,
        to_tier=tier,
        expires_at=expires_at,
        reason=reason.strip(),
        granted_by=granted_by,
    )

    account.tier = tier
    # Free has nothing to expire. Leaving a stale date behind would be harmless
    # today and confusing the moment somebody reads the row.
    account.tier_expires_at = None if tier == "free" else expires_at
    account.tier_source = "manual"
    account.tier_updated_at = now
    account.tier_updated_by = granted_by

    db.add(record)
    db.add(account)
    db.commit()
    db.refresh(record)
    db.refresh(account)

    log.info(
        "Admin %s moved account %s from %s to %s (%s)",
        granted_by,
        account.id,
        record.from_tier,
        record.to_tier,
        reason,
    )
    return record


def history(db: Session, account_id: int) -> list[StatPitchTierGrant]:
    """Every grant for an account, newest first."""
    return db.exec(
        select(StatPitchTierGrant)
        .where(StatPitchTierGrant.account_id == account_id)
        .order_by(col(StatPitchTierGrant.granted_at).desc(), col(StatPitchTierGrant.id).desc())
    ).all()
