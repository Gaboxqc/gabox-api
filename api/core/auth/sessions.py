"""Session token lifecycle and login-attempt throttling."""

import hashlib
import secrets
from datetime import timedelta

from sqlmodel import Session, col, delete, func, select

from api.core.auth.models import AdminSession, AdminUser, LoginAttempt, utcnow
from api.core.config import settings

# 32 bytes of CSPRNG output — 256 bits, well beyond guessing range.
_TOKEN_BYTES = 32


def generate_token() -> str:
    return secrets.token_urlsafe(_TOKEN_BYTES)


def hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def create_session(
    db: Session,
    user: AdminUser,
    *,
    ip_address: str | None,
    user_agent: str | None,
) -> tuple[AdminSession, str]:
    """Open a new session and return it alongside the *raw* token.

    The raw token is returned once and never stored; only its hash is
    persisted. A fresh row per login means the token rotates on every sign-in.
    """
    raw_token = generate_token()
    now = utcnow()

    session = AdminSession(
        token_hash=hash_token(raw_token),
        csrf_token=secrets.token_urlsafe(_TOKEN_BYTES),
        user_id=user.id,
        created_at=now,
        last_used_at=now,
        expires_at=now + timedelta(seconds=settings.session_max_age_seconds),
        ip_address=ip_address,
        user_agent=(user_agent or None) and user_agent[:256],
    )
    db.add(session)

    user.last_login_at = now
    db.add(user)

    db.commit()
    db.refresh(session)
    return session, raw_token


def load_valid_session(db: Session, raw_token: str) -> AdminSession | None:
    """Return the live session for `raw_token`, or None.

    Rejects sessions that are revoked, past their absolute expiry, or idle for
    longer than the configured window. The idle check is what limits the damage
    from a token captured off an unattended machine.
    """
    session = db.exec(
        select(AdminSession).where(AdminSession.token_hash == hash_token(raw_token))
    ).first()

    if session is None or session.revoked_at is not None:
        return None

    now = utcnow()
    if session.expires_at <= now:
        return None

    idle_cutoff = session.last_used_at + timedelta(seconds=settings.session_idle_seconds)
    if idle_cutoff <= now:
        return None

    return session


def touch_session(db: Session, session: AdminSession) -> None:
    """Slide the idle window forward."""
    session.last_used_at = utcnow()
    db.add(session)
    db.commit()


def revoke_session(db: Session, session: AdminSession) -> None:
    session.revoked_at = utcnow()
    db.add(session)
    db.commit()


def revoke_all_sessions(db: Session, user_id: int) -> int:
    """Revoke every live session for a user. Returns how many were closed."""
    live = db.exec(
        select(AdminSession).where(
            AdminSession.user_id == user_id,
            col(AdminSession.revoked_at).is_(None),
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
    return utcnow() - timedelta(seconds=settings.login_attempt_window_seconds)


def recent_failure_count(db: Session, username: str, ip_address: str) -> int:
    """Failed attempts in the current window for this username *or* this IP.

    Counting both, rather than the pair, means an attacker cannot sidestep the
    limit by rotating usernames from one address or by spraying one username
    from many addresses.
    """
    statement = (
        select(func.count())
        .select_from(LoginAttempt)
        .where(
            col(LoginAttempt.succeeded).is_(False),
            LoginAttempt.attempted_at >= _attempt_window_start(),
            col(LoginAttempt.username) == username,
        )
    )
    by_username = db.exec(statement).one()

    statement = (
        select(func.count())
        .select_from(LoginAttempt)
        .where(
            col(LoginAttempt.succeeded).is_(False),
            LoginAttempt.attempted_at >= _attempt_window_start(),
            col(LoginAttempt.ip_address) == ip_address,
        )
    )
    by_ip = db.exec(statement).one()

    return max(int(by_username), int(by_ip))


def is_locked_out(db: Session, username: str, ip_address: str) -> bool:
    return recent_failure_count(db, username, ip_address) >= settings.login_max_attempts


def record_attempt(db: Session, username: str, ip_address: str, *, succeeded: bool) -> None:
    db.add(
        LoginAttempt(
            username=username[:64],
            ip_address=ip_address[:45],
            succeeded=succeeded,
        )
    )
    db.commit()


def clear_failures(db: Session, username: str, ip_address: str) -> None:
    """Drop the failure history after a success, so one bad day does not leave
    the account throttled for the rest of the window."""
    db.exec(
        delete(LoginAttempt).where(
            col(LoginAttempt.succeeded).is_(False),
            col(LoginAttempt.username) == username,
            col(LoginAttempt.ip_address) == ip_address,
        )
    )
    db.commit()


def prune_old_attempts(db: Session) -> None:
    """Delete attempts too old to matter.

    Called opportunistically on login instead of from a scheduled job, which a
    serverless deployment has no natural place to run.
    """
    cutoff = utcnow() - timedelta(seconds=settings.login_attempt_window_seconds * 8)
    db.exec(delete(LoginAttempt).where(LoginAttempt.attempted_at < cutoff))
    db.commit()
