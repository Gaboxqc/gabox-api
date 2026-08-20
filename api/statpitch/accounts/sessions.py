"""Customer session lifecycle and login-attempt throttling.

Mirrors `api.core.auth.sessions` in shape but not in configuration: these
sessions are long (30 days absolute, 7 idle) because a StatPitch customer checks
the app on match day, while the admin's are deliberately short. See
`accounts/models.py` for why the two are kept apart rather than shared.
"""

import hashlib
import secrets
from datetime import timedelta

from sqlmodel import Session, col, delete, func, select

from api.core.config import settings
from api.statpitch.accounts.models import (
    StatPitchAccount,
    StatPitchAccountSession,
    StatPitchLoginAttempt,
    utcnow,
)

# 32 bytes of CSPRNG output — 256 bits, well beyond guessing range.
_TOKEN_BYTES = 32


def generate_token() -> str:
    return secrets.token_urlsafe(_TOKEN_BYTES)


def hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def normalise_email(email: str) -> str:
    """The single definition of what makes two addresses the same account.

    Lowercase and trimmed, nothing cleverer. Stripping dots or `+tags` would be
    Gmail-specific behaviour applied to every provider, and it would stop people
    using `me+statpitch@` to filter their own mail — which is a legitimate thing
    to want, not an evasion.
    """
    return email.strip().lower()


def create_session(
    db: Session,
    account: StatPitchAccount,
    *,
    ip_address: str | None,
    user_agent: str | None,
) -> tuple[StatPitchAccountSession, str]:
    """Open a new session and return it alongside the *raw* token.

    The raw token is returned once and never stored; only its hash is persisted.
    A fresh row per login means the token rotates on every sign-in.
    """
    raw_token = generate_token()
    now = utcnow()

    session = StatPitchAccountSession(
        token_hash=hash_token(raw_token),
        csrf_token=secrets.token_urlsafe(_TOKEN_BYTES),
        account_id=account.id,
        created_at=now,
        last_used_at=now,
        expires_at=now + timedelta(seconds=settings.statpitch_session_max_age_seconds),
        ip_address=ip_address,
        user_agent=(user_agent or None) and user_agent[:256],
    )
    db.add(session)

    account.last_login_at = now
    db.add(account)

    db.commit()
    db.refresh(session)
    return session, raw_token


def load_valid_session(db: Session, raw_token: str) -> StatPitchAccountSession | None:
    """Return the live session for `raw_token`, or None.

    Rejects sessions that are revoked, past their absolute expiry, or idle for
    longer than the configured window.
    """
    session = db.exec(
        select(StatPitchAccountSession).where(
            StatPitchAccountSession.token_hash == hash_token(raw_token)
        )
    ).first()

    if session is None or session.revoked_at is not None:
        return None

    now = utcnow()
    if session.expires_at <= now:
        return None

    idle_cutoff = session.last_used_at + timedelta(seconds=settings.statpitch_session_idle_seconds)
    if idle_cutoff <= now:
        return None

    return session


def touch_session(db: Session, session: StatPitchAccountSession) -> None:
    """Slide the idle window forward.

    A write on every authenticated read, which would be worth avoiding at real
    volume. It is kept because the alternative — never sliding — turns the idle
    window into a second absolute expiry and logs active users out mid-session.
    """
    session.last_used_at = utcnow()
    db.add(session)
    db.commit()


def revoke_session(db: Session, session: StatPitchAccountSession) -> None:
    session.revoked_at = utcnow()
    db.add(session)
    db.commit()


def revoke_all_sessions(db: Session, account_id: int) -> int:
    """Revoke every live session for an account. Returns how many were closed.

    Used on password change: a reset after a suspected compromise that leaves
    the attacker's session open has achieved nothing.
    """
    live = db.exec(
        select(StatPitchAccountSession).where(
            StatPitchAccountSession.account_id == account_id,
            col(StatPitchAccountSession.revoked_at).is_(None),
        )
    ).all()
    now = utcnow()
    for session in live:
        session.revoked_at = now
        db.add(session)
    db.commit()
    return len(live)


# ── Login throttling ─────────────────────────────────────────────────────────


def _attempt_window_start():
    return utcnow() - timedelta(seconds=settings.statpitch_login_attempt_window_seconds)


def recent_failure_count(db: Session, email: str, ip_address: str) -> int:
    """Failed attempts in the current window for this email *or* this address.

    Counting both, rather than the pair, means an attacker cannot sidestep the
    limit by rotating addresses from one IP or by spraying one address from
    many IPs.
    """
    window_start = _attempt_window_start()

    by_email = db.exec(
        select(func.count())
        .select_from(StatPitchLoginAttempt)
        .where(
            col(StatPitchLoginAttempt.succeeded).is_(False),
            StatPitchLoginAttempt.attempted_at >= window_start,
            col(StatPitchLoginAttempt.email) == email,
        )
    ).one()

    by_ip = db.exec(
        select(func.count())
        .select_from(StatPitchLoginAttempt)
        .where(
            col(StatPitchLoginAttempt.succeeded).is_(False),
            StatPitchLoginAttempt.attempted_at >= window_start,
            col(StatPitchLoginAttempt.ip_address) == ip_address,
        )
    ).one()

    return max(int(by_email), int(by_ip))


def is_locked_out(db: Session, email: str, ip_address: str) -> bool:
    return recent_failure_count(db, email, ip_address) >= settings.statpitch_login_max_attempts


def record_attempt(db: Session, email: str, ip_address: str, *, succeeded: bool) -> None:
    db.add(
        StatPitchLoginAttempt(
            email=email[:254],
            ip_address=ip_address[:45],
            succeeded=succeeded,
        )
    )
    db.commit()


def clear_failures(db: Session, email: str, ip_address: str) -> None:
    """Drop the failure history after a success, so one forgotten password does
    not leave the account throttled for the rest of the window."""
    db.exec(
        delete(StatPitchLoginAttempt).where(
            col(StatPitchLoginAttempt.succeeded).is_(False),
            col(StatPitchLoginAttempt.email) == email,
            col(StatPitchLoginAttempt.ip_address) == ip_address,
        )
    )
    db.commit()


def prune_old_attempts(db: Session) -> None:
    """Delete attempts too old to matter.

    Called opportunistically on login instead of from a scheduled job, which a
    serverless deployment has no natural place to run.
    """
    cutoff = utcnow() - timedelta(seconds=settings.statpitch_login_attempt_window_seconds * 8)
    db.exec(delete(StatPitchLoginAttempt).where(StatPitchLoginAttempt.attempted_at < cutoff))
    db.commit()
