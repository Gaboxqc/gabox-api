"""The free tier's three predictions a day.

A counter cannot live in memory: the API runs serverless, so every invocation is
a fresh process and an in-process tally would reset continuously and enforce
nothing. Same reasoning as the login lockout.

**What is rationed is the prediction, not the fixture.** A free account always
sees who is playing, when, and the crests — that is the shape of the product.
What costs an unlock is revealing the model's 1X2 probabilities, which is the
line the pricing page actually sells.

**Unlocks are permanent, and counted by the day they were spent.** Opening the
same fixture twice costs one unlock, not two, and opening it again tomorrow
costs nothing — the row already exists. Anything else punishes a reader for
refreshing the page, which is not a behaviour worth pricing.

**Anonymous visitors get nothing to unlock.** There is no honest way to count
three per day against somebody with no account: cookies clear and addresses
rotate. So they see the teaser, and signing up is what reveals a prediction.
That also makes registration the conversion step rather than an afterthought.
"""

import logging
from datetime import UTC, date, datetime

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, Session, SQLModel, col, func, select

from api.statpitch.accounts.models import StatPitchAccount, Tier
from api.statpitch.tiers import policy_for

log = logging.getLogger("statpitch.quota")


class StatPitchPredictionUnlock(SQLModel, table=True):
    """One fixture whose prediction an account has revealed.

    Append-only. Keyed on `fixture_id` rather than the fixture's primary key
    because the fixture table is a three-day cache: a row can be pruned and
    reappear with a new id, and an unlock the reader already paid for must not
    evaporate with it.
    """

    __tablename__: str = "statpitch_prediction_unlock"
    __table_args__ = (
        UniqueConstraint("account_id", "fixture_id", name="uq_statpitch_unlock_account_fixture"),
    )

    id: int | None = Field(default=None, primary_key=True)
    account_id: int = Field(foreign_key="statpitch_account.id", ondelete="CASCADE", index=True)
    fixture_id: str = Field(index=True, max_length=128)
    # The Nicaragua-local day the unlock was spent on, so the daily allowance
    # rolls over when the app's "today" does rather than at UTC midnight.
    unlocked_on: date = Field(index=True)
    unlocked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


def unlocked_ids(db: Session, account: StatPitchAccount | None) -> set[str]:
    """Every fixture this account has ever unlocked.

    Fetched in one query and handed to the serialiser, so rendering a list of
    forty fixtures does not become forty lookups.
    """
    if account is None or account.id is None:
        return set()

    rows = db.exec(
        select(StatPitchPredictionUnlock.fixture_id).where(
            StatPitchPredictionUnlock.account_id == account.id
        )
    ).all()
    return set(rows)


def spent_today(db: Session, account: StatPitchAccount, day: date) -> int:
    return int(
        db.exec(
            select(func.count())
            .select_from(StatPitchPredictionUnlock)
            .where(
                StatPitchPredictionUnlock.account_id == account.id,
                col(StatPitchPredictionUnlock.unlocked_on) == day,
            )
        ).one()
    )


def remaining(db: Session, account: StatPitchAccount | None, tier: Tier, day: date) -> int | None:
    """How many unlocks are left today. None means unlimited.

    `tier` is passed rather than read off the account, because the two can
    legitimately disagree: the master key is Elite and has no account at all.
    Deriving the tier here would report it as having spent its whole allowance.

    Anonymous callers on a rationed tier get 0 rather than None — an allowance
    of nothing is not the same as having no limit.
    """
    limit = policy_for(tier).daily_predictions
    if limit is None:
        return None
    if account is None:
        return 0
    return max(0, limit - spent_today(db, account, day))


def is_unlocked(db: Session, account: StatPitchAccount | None, fixture_id: str) -> bool:
    if account is None or account.id is None:
        return False
    return (
        db.exec(
            select(StatPitchPredictionUnlock).where(
                StatPitchPredictionUnlock.account_id == account.id,
                StatPitchPredictionUnlock.fixture_id == fixture_id,
            )
        ).first()
        is not None
    )


def unlock(
    db: Session,
    account: StatPitchAccount | None,
    tier: Tier,
    fixture_id: str,
    day: date,
) -> bool:
    """Reveal a fixture's prediction to this account. Returns whether it is now
    visible.

    Idempotent: a fixture already unlocked returns True without spending
    anything, which is what stops a page refresh costing a reader an allowance.

    Unlimited tiers are never recorded. Writing a row per fixture view for every
    Pro subscriber would grow a table nothing ever reads, and `remaining()`
    already answers None for them.
    """
    # Unlimited first, and before the account check: the master key has no
    # account but is never rationed.
    limit = policy_for(tier).daily_predictions
    if limit is None:
        return True

    if account is None:
        return False

    if is_unlocked(db, account, fixture_id):
        return True

    if spent_today(db, account, day) >= limit:
        return False

    db.add(
        StatPitchPredictionUnlock(
            account_id=account.id,
            fixture_id=fixture_id,
            unlocked_on=day,
        )
    )
    db.commit()
    log.info("Account %s unlocked fixture %s", account.id, fixture_id)
    return True
