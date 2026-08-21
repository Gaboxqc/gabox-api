"""Account-scoped API keys — the one thing Elite buys over Pro.

Presented as `Authorization: Bearer sp_live_…`, deliberately *not* as
`X-API-KEY`. That header already means the master key, which can write to the
whole API; a customer key can only read StatPitch. Two credentials with
different powers sharing one header is how a check ends up being made against
the wrong one.

Only the SHA-256 of a key is stored, for the same reason as session tokens: a
database leak must not hand over live credentials. SHA-256 rather than argon2
because the key is 256 bits of CSPRNG output — there is nothing to brute-force,
and this runs on every request.

The `sp_live_` prefix is not decoration. It makes a leaked key recognisable in a
log or a public repository, which is what lets secret scanners flag it, and it
tells a support conversation which product the string came from.
"""

import hashlib
import logging
import secrets
from datetime import UTC, datetime

from sqlmodel import Field, Session, SQLModel, col, select

log = logging.getLogger("statpitch.keys")

KEY_PREFIX = "sp_live_"
_KEY_BYTES = 32
# Enough of the key to identify a row in a list without being enough to use.
_DISPLAY_LENGTH = len(KEY_PREFIX) + 8


class StatPitchApiKey(SQLModel, table=True):
    """One issued key. Revoked rather than deleted, so a key that turns up in a
    log later can still be identified."""

    __tablename__: str = "statpitch_api_key"

    id: int | None = Field(default=None, primary_key=True)
    account_id: int = Field(foreign_key="statpitch_account.id", ondelete="CASCADE", index=True)
    # What the owner called it — "staging", "my bot". Purely for their own
    # benefit when deciding which one to revoke.
    name: str = Field(max_length=64)
    # The visible stub, e.g. `sp_live_A1b2C3d4`. Stored rather than derived
    # because the rest of the key is gone the moment it is issued.
    prefix: str = Field(max_length=32)
    key_hash: str = Field(unique=True, index=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    # Written at most once a day — see `touch`. Enough to answer "is this one
    # still in use?" without a write on every request.
    last_used_at: datetime | None = Field(default=None)
    revoked_at: datetime | None = Field(default=None)


def hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def generate_key() -> tuple[str, str, str]:
    """Return `(raw_key, prefix, key_hash)`.

    The raw key is returned once, here, and never again — nothing stores it.
    """
    raw_key = f"{KEY_PREFIX}{secrets.token_urlsafe(_KEY_BYTES)}"
    return raw_key, raw_key[:_DISPLAY_LENGTH], hash_key(raw_key)


def issue(db: Session, account_id: int, name: str) -> tuple[StatPitchApiKey, str]:
    raw_key, prefix, key_hash = generate_key()

    key = StatPitchApiKey(
        account_id=account_id,
        name=name,
        prefix=prefix,
        key_hash=key_hash,
    )
    db.add(key)
    db.commit()
    db.refresh(key)

    log.info("Issued API key %s for account %s", key.prefix, account_id)
    return key, raw_key


def load_live_key(db: Session, raw_key: str) -> StatPitchApiKey | None:
    """The live key matching `raw_key`, or None.

    Looked up by hash, so the comparison is an indexed equality rather than a
    scan — and a wrong key costs the same as a right one.
    """
    if not raw_key.startswith(KEY_PREFIX):
        return None

    key = db.exec(
        select(StatPitchApiKey).where(StatPitchApiKey.key_hash == hash_key(raw_key))
    ).first()
    if key is None or key.revoked_at is not None:
        return None
    return key


def touch(db: Session, key: StatPitchApiKey) -> None:
    """Record that the key was used, at most once a day.

    `last_used_at` exists to answer "can I safely revoke this one?", which does
    not need minute precision. Writing on every request would put a database
    write in front of every read for the tier that is most likely to be
    hammering the API.
    """
    now = datetime.now(UTC)
    last = key.last_used_at
    if last is not None and last.replace(tzinfo=UTC).date() == now.date():
        return

    key.last_used_at = now
    db.add(key)
    db.commit()


def revoke(db: Session, key: StatPitchApiKey) -> None:
    key.revoked_at = datetime.now(UTC)
    db.add(key)
    db.commit()
    log.info("Revoked API key %s", key.prefix)


def list_for(db: Session, account_id: int) -> list[StatPitchApiKey]:
    return db.exec(
        select(StatPitchApiKey)
        .where(StatPitchApiKey.account_id == account_id)
        .order_by(col(StatPitchApiKey.created_at).desc())
    ).all()
