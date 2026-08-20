"""StatPitch customer accounts: session lifecycle, throttling, tier expiry.

The routes come next; this pins down the machinery underneath them, plus the
boundary that matters most — a customer session must be worthless against the
admin surface.
"""

from datetime import timedelta

import pytest
from sqlmodel import Session, select

from api.core.auth.passwords import hash_password
from api.core.config import settings
from api.statpitch.accounts.models import (
    StatPitchAccount,
    StatPitchAccountSession,
    StatPitchLoginAttempt,
    utcnow,
)
from api.statpitch.accounts.sessions import (
    clear_failures,
    create_session,
    hash_token,
    is_locked_out,
    load_valid_session,
    normalise_email,
    prune_old_attempts,
    recent_failure_count,
    record_attempt,
    revoke_all_sessions,
    revoke_session,
    touch_session,
)

PASSWORD = "correct-horse-battery-staple"


@pytest.fixture(name="account")
def account_fixture(engine):
    with Session(engine) as db:
        account = StatPitchAccount(
            email="bettor@example.com",
            password_hash=hash_password(PASSWORD),
            tier="pro",
        )
        db.add(account)
        db.commit()
        db.refresh(account)
        return account.id


# ── Tier ─────────────────────────────────────────────────────────────────────


def test_tier_without_an_expiry_is_perpetual():
    assert (
        StatPitchAccount(email="a@b.c", password_hash="x", tier="elite").effective_tier == "elite"
    )


def test_expired_tier_reads_as_free():
    """The whole point of `effective_tier`: no cron job demotes anyone, so a
    lapsed subscription has to stop working by being read correctly."""
    account = StatPitchAccount(
        email="a@b.c",
        password_hash="x",
        tier="pro",
        tier_expires_at=utcnow() - timedelta(seconds=1),
    )
    assert account.effective_tier == "free"


def test_unexpired_tier_survives():
    account = StatPitchAccount(
        email="a@b.c",
        password_hash="x",
        tier="pro",
        tier_expires_at=utcnow() + timedelta(days=1),
    )
    assert account.effective_tier == "pro"


def test_free_stays_free_whatever_the_expiry():
    account = StatPitchAccount(
        email="a@b.c",
        password_hash="x",
        tier="free",
        tier_expires_at=utcnow() - timedelta(days=30),
    )
    assert account.effective_tier == "free"


def test_a_new_account_defaults_to_the_weakest_position(engine):
    """A row that says nothing about its tier must not be a paying one."""
    with Session(engine) as db:
        account = StatPitchAccount(email="quiet@example.com", password_hash="x")
        db.add(account)
        db.commit()
        db.refresh(account)

    assert account.tier == "free"
    assert account.effective_tier == "free"
    assert account.tier_source == "manual"


# ── Email identity ───────────────────────────────────────────────────────────


def test_email_normalisation_is_case_and_space_insensitive():
    assert normalise_email("  Bettor@Example.COM ") == "bettor@example.com"


def test_plus_tags_are_preserved():
    """`me+statpitch@` is how people filter their own mail, not an evasion."""
    assert normalise_email("me+statpitch@example.com") == "me+statpitch@example.com"


def test_the_same_email_cannot_be_registered_twice(engine, account):
    from sqlalchemy.exc import IntegrityError

    with Session(engine) as db:
        db.add(StatPitchAccount(email="bettor@example.com", password_hash="x"))
        with pytest.raises(IntegrityError):
            db.commit()


# ── Session lifecycle ────────────────────────────────────────────────────────


def test_only_the_token_hash_is_stored(engine, account):
    """A database leak must not hand over live sessions."""
    with Session(engine) as db:
        row = db.get(StatPitchAccount, account)
        session, raw_token = create_session(db, row, ip_address="1.2.3.4", user_agent="pytest")
        stored = session.token_hash

    assert stored == hash_token(raw_token)
    assert raw_token not in stored


def test_login_stamps_last_login_at(engine, account):
    with Session(engine) as db:
        row = db.get(StatPitchAccount, account)
        assert row.last_login_at is None
        create_session(db, row, ip_address=None, user_agent=None)

    with Session(engine) as db:
        assert db.get(StatPitchAccount, account).last_login_at is not None


def test_a_live_token_loads(engine, account):
    with Session(engine) as db:
        row = db.get(StatPitchAccount, account)
        _, raw_token = create_session(db, row, ip_address=None, user_agent=None)
        assert load_valid_session(db, raw_token) is not None


def test_an_unknown_token_loads_nothing(engine, account):
    with Session(engine) as db:
        assert load_valid_session(db, "not-a-real-token") is None


def test_a_revoked_session_stops_working(engine, account):
    with Session(engine) as db:
        row = db.get(StatPitchAccount, account)
        session, raw_token = create_session(db, row, ip_address=None, user_agent=None)
        revoke_session(db, session)
        assert load_valid_session(db, raw_token) is None


def test_the_absolute_expiry_is_enforced(engine, account):
    """Refreshing cannot extend a session past this — it is the ceiling."""
    with Session(engine) as db:
        row = db.get(StatPitchAccount, account)
        session, raw_token = create_session(db, row, ip_address=None, user_agent=None)
        session.expires_at = utcnow() - timedelta(seconds=1)
        db.add(session)
        db.commit()
        assert load_valid_session(db, raw_token) is None


def test_the_idle_window_is_enforced(engine, account):
    """Limits the damage from a token captured off an unattended machine."""
    with Session(engine) as db:
        row = db.get(StatPitchAccount, account)
        session, raw_token = create_session(db, row, ip_address=None, user_agent=None)
        session.last_used_at = utcnow() - timedelta(
            seconds=settings.statpitch_session_idle_seconds + 60
        )
        db.add(session)
        db.commit()
        assert load_valid_session(db, raw_token) is None


def test_touching_a_session_slides_the_idle_window(engine, account):
    with Session(engine) as db:
        row = db.get(StatPitchAccount, account)
        session, raw_token = create_session(db, row, ip_address=None, user_agent=None)
        stale = utcnow() - timedelta(seconds=settings.statpitch_session_idle_seconds - 60)
        session.last_used_at = stale
        db.add(session)
        db.commit()

        touch_session(db, session)
        assert session.last_used_at > stale
        assert load_valid_session(db, raw_token) is not None


def test_every_login_mints_a_fresh_token(engine, account):
    with Session(engine) as db:
        row = db.get(StatPitchAccount, account)
        _, first = create_session(db, row, ip_address=None, user_agent=None)
        _, second = create_session(db, row, ip_address=None, user_agent=None)

    assert first != second


def test_revoking_all_sessions_closes_every_live_one(engine, account):
    with Session(engine) as db:
        row = db.get(StatPitchAccount, account)
        tokens = [create_session(db, row, ip_address=None, user_agent=None)[1] for _ in range(3)]

        assert revoke_all_sessions(db, account) == 3
        assert all(load_valid_session(db, token) is None for token in tokens)
        # Idempotent: nothing is left live to close.
        assert revoke_all_sessions(db, account) == 0


def test_deleting_an_account_takes_its_sessions_with_it(engine, account):
    with Session(engine) as db:
        row = db.get(StatPitchAccount, account)
        create_session(db, row, ip_address=None, user_agent=None)
        db.delete(row)
        db.commit()

        assert db.exec(select(StatPitchAccountSession)).all() == []


# ── Throttling ───────────────────────────────────────────────────────────────


def test_failures_accumulate_until_the_account_is_locked(engine):
    with Session(engine) as db:
        for _ in range(settings.statpitch_login_max_attempts - 1):
            record_attempt(db, "target@example.com", "10.0.0.1", succeeded=False)
        assert is_locked_out(db, "target@example.com", "10.0.0.1") is False

        record_attempt(db, "target@example.com", "10.0.0.1", succeeded=False)
        assert is_locked_out(db, "target@example.com", "10.0.0.1") is True


def test_rotating_the_address_does_not_reset_the_count(engine):
    """Spraying one account from many IPs must still trip the limit."""
    with Session(engine) as db:
        for index in range(settings.statpitch_login_max_attempts):
            record_attempt(db, "target@example.com", f"10.0.0.{index}", succeeded=False)

        assert is_locked_out(db, "target@example.com", "10.0.0.99") is True


def test_rotating_the_email_does_not_reset_the_count(engine):
    """Nor may one address work through a list of addresses."""
    with Session(engine) as db:
        for index in range(settings.statpitch_login_max_attempts):
            record_attempt(db, f"target{index}@example.com", "10.0.0.1", succeeded=False)

        assert is_locked_out(db, "fresh@example.com", "10.0.0.1") is True


def test_a_success_clears_the_history(engine):
    """One forgotten password must not leave the account throttled all window."""
    with Session(engine) as db:
        for _ in range(3):
            record_attempt(db, "target@example.com", "10.0.0.1", succeeded=False)

        clear_failures(db, "target@example.com", "10.0.0.1")
        assert recent_failure_count(db, "target@example.com", "10.0.0.1") == 0


def test_attempts_outside_the_window_do_not_count(engine):
    with Session(engine) as db:
        old = StatPitchLoginAttempt(
            email="target@example.com",
            ip_address="10.0.0.1",
            succeeded=False,
            attempted_at=utcnow()
            - timedelta(seconds=settings.statpitch_login_attempt_window_seconds + 60),
        )
        db.add(old)
        db.commit()

        assert recent_failure_count(db, "target@example.com", "10.0.0.1") == 0


def test_pruning_drops_attempts_too_old_to_matter(engine):
    with Session(engine) as db:
        db.add(
            StatPitchLoginAttempt(
                email="ancient@example.com",
                ip_address="10.0.0.1",
                succeeded=False,
                attempted_at=utcnow() - timedelta(days=365),
            )
        )
        db.add(
            StatPitchLoginAttempt(
                email="recent@example.com", ip_address="10.0.0.2", succeeded=False
            )
        )
        db.commit()

        prune_old_attempts(db)
        remaining = db.exec(select(StatPitchLoginAttempt)).all()

    assert [row.email for row in remaining] == ["recent@example.com"]


# ── Isolation from the admin ─────────────────────────────────────────────────


def test_a_customer_token_is_worthless_against_the_admin_surface(client, engine, account):
    """The two systems share no table, so the admin path cannot even find this
    session — but that is exactly the sort of guarantee worth a test rather than
    an argument."""
    with Session(engine) as db:
        row = db.get(StatPitchAccount, account)
        _, raw_token = create_session(db, row, ip_address=None, user_agent=None)

    # Presented under the admin's own cookie name, which is the strongest form
    # of the confusion this is guarding against.
    client.cookies.set(settings.session_cookie_name, raw_token)
    response = client.post("/portfolio/tags", json={"name": "Crystal"})

    assert response.status_code == 401


def test_customer_lockout_cannot_lock_the_admin_out(engine):
    """Separate attempt tables: a customer being brute-forced must not cost
    Gabriel access to the dashboard."""
    from api.core.auth.sessions import is_locked_out as admin_is_locked_out

    with Session(engine) as db:
        for index in range(settings.statpitch_login_max_attempts * 3):
            record_attempt(db, f"victim{index}@example.com", "10.0.0.1", succeeded=False)

        assert is_locked_out(db, "victim0@example.com", "10.0.0.1") is True
        assert admin_is_locked_out(db, "gabox", "10.0.0.1") is False
