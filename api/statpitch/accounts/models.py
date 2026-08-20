"""Tables backing StatPitch customer accounts.

Deliberately separate from the admin tables in `api.core.auth`, not a widening
of them. Three reasons, in order of how much they matter:

1. **A customer must never be able to become an admin.** Sharing one table makes
   that a matter of a `role` column being read correctly on every path. Sharing
   nothing makes it structurally impossible — there is no row a customer holds
   that any admin dependency will ever look at.
2. **The two have different session lifetimes.** The dashboard wants 12 hours
   absolute and 2 idle, because it can write to the portfolio. A consumer app
   people check on match day wants 30 days and 7, because forcing a re-login
   every afternoon is how a subscription gets cancelled.
3. **Blast radius.** A mistake in one login path cannot reach the other.

What is *not* duplicated is the argon2 configuration: this module imports
`api.core.auth.passwords` rather than standing up a second hasher, because two
sets of cost parameters drifting apart is a real cost with no upside.

Every timestamp here is *naive UTC*, matching the admin tables and for the same
reason: SQLite — which the test suite runs on — cannot round-trip a
timezone-aware datetime, so a tz-aware column comes back naive and every
`aware < naive` comparison raises TypeError.
"""

import re
from datetime import UTC, datetime
from typing import Literal

from pydantic import field_validator
from sqlmodel import Field, Relationship, SQLModel

# What a customer has paid for. Ordered weakest to strongest — `TIER_ORDER` is
# relied on for "at least Pro" style comparisons, so keep it that way.
Tier = Literal["free", "pro", "elite"]
TIER_ORDER: tuple[Tier, ...] = ("free", "pro", "elite")

# How an account came by its tier.
#   manual — granted from the admin dashboard (Elite is sold by conversation)
#   trial  — the self-serve 14-day Pro trial
#   stripe — not reachable yet; exists so wiring billing up later is a write to
#            this column rather than a migration
TierSource = Literal["manual", "trial", "stripe"]

# Columns are plain `str` rather than a database ENUM, matching the rest of the
# StatPitch schema: adding a value to a PostgreSQL enum inside a transaction is
# a migration hazard, and the SQLite the tests run on has no enums at all.


def utcnow() -> datetime:
    """Current UTC time, naive. See the module docstring for why."""
    return datetime.now(UTC).replace(tzinfo=None)


class StatPitchAccount(SQLModel, table=True):
    """One customer. Identified by email — there is no username here.

    Nothing in this table grants any administrative power. The admin account
    lives in `admin_user` and is reached through `api.core.auth`; the two never
    meet.
    """

    __tablename__: str = "statpitch_account"

    id: int | None = Field(default=None, primary_key=True)
    # Stored lowercased so that Gabriel@x.com and gabriel@x.com cannot become
    # two accounts. Normalisation happens on the way in, at the router, so the
    # unique index is the thing actually enforcing it.
    email: str = Field(unique=True, index=True, max_length=254)
    # argon2id PHC string: algorithm, parameters and salt travel with the hash,
    # so the parameters can be raised later without invalidating old passwords.
    password_hash: str
    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=utcnow)
    last_login_at: datetime | None = Field(default=None)
    email_verified_at: datetime | None = Field(default=None)

    # ── Entitlement ───────────────────────────────────────────────────────────
    # Never read this column directly — use `effective_tier`, which honours the
    # expiry. Reading it raw is how an expired subscription keeps working.
    tier: str = Field(default="free", index=True)
    # Null means perpetual, which is what a manually granted tier gets unless an
    # end date is set deliberately.
    tier_expires_at: datetime | None = Field(default=None)
    tier_source: str = Field(default="manual", max_length=16)
    tier_updated_at: datetime | None = Field(default=None)
    # The admin username that granted it, stored as text rather than a foreign
    # key into `admin_user`: this module does not depend on that table, and a
    # grant should stay explicable even if the admin who made it is removed.
    tier_updated_by: str | None = Field(default=None, max_length=64)
    # Stamped when the 14-day Pro trial is taken, so it can only be taken once.
    trial_used_at: datetime | None = Field(default=None)

    sessions: list["StatPitchAccountSession"] = Relationship(
        back_populates="account", cascade_delete=True
    )

    @property
    def effective_tier(self) -> Tier:
        """The tier this account actually has right now.

        An expired grant reads as `free` everywhere, immediately and with no
        scheduled job to demote it — which matters because a serverless
        deployment has no natural place to run one. It also means a trial simply
        stops being Pro when it runs out, rather than needing to be cleaned up.
        """
        if self.tier == "free":
            return "free"
        if self.tier_expires_at is not None and self.tier_expires_at <= utcnow():
            return "free"
        return self.tier  # type: ignore[return-value]


class StatPitchAccountSession(SQLModel, table=True):
    """One row per active customer login.

    Server-side sessions rather than JWTs, for the same reason the admin uses
    them: a JWT cannot be revoked, so a stolen one stays valid until it expires.
    A row here can be revoked immediately, and the lookup costs nothing because
    every request already talks to Postgres.
    """

    __tablename__: str = "statpitch_account_session"

    id: int | None = Field(default=None, primary_key=True)
    # Only the SHA-256 of the token is stored, so a database leak does not hand
    # over live sessions. SHA-256 rather than argon2 because the token is 256
    # bits of CSPRNG output — there is nothing to brute-force, and this runs on
    # every authenticated request.
    token_hash: str = Field(unique=True, index=True)
    # Compared against the X-CSRF-Token header on unsafe methods. Held server
    # side so this is a real check, not just cookie-vs-header agreement.
    csrf_token: str
    account_id: int = Field(foreign_key="statpitch_account.id", ondelete="CASCADE", index=True)
    created_at: datetime = Field(default_factory=utcnow)
    last_used_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime
    revoked_at: datetime | None = Field(default=None)
    ip_address: str | None = Field(default=None, max_length=45)
    user_agent: str | None = Field(default=None, max_length=256)

    account: StatPitchAccount = Relationship(back_populates="sessions")


class StatPitchLoginAttempt(SQLModel, table=True):
    """Audit trail for the lockout check.

    Kept in the database rather than in memory because the API runs on
    serverless functions: each invocation is a fresh process, so an in-process
    counter would reset continuously and enforce nothing.

    Separate from `admin_login_attempt` so that a customer being brute-forced
    cannot lock the admin out of the dashboard, and vice versa.
    """

    __tablename__: str = "statpitch_login_attempt"

    id: int | None = Field(default=None, primary_key=True)
    # The address that was *tried*, which may match no account at all — that is
    # precisely what this table exists to count, so it is not a foreign key.
    email: str = Field(index=True, max_length=254)
    ip_address: str = Field(index=True, max_length=45)
    succeeded: bool = Field(default=False)
    attempted_at: datetime = Field(default_factory=utcnow, index=True)


# ==============================================================================
# REQUEST AND READ SCHEMAS  (not tables)
# ==============================================================================

# Deliberately not `pydantic.EmailStr`: that pulls in email-validator and
# dnspython, which is a megabyte of serverless bundle to answer a question this
# cannot really answer anyway. Whether an address exists is settled by sending
# mail to it, not by parsing — so this only rejects input that is obviously not
# an address, and verification is left to the verification email.
_EMAIL_SHAPE = re.compile(r"^[^@\s]+@[^@\s.]+(\.[^@\s.]+)+$")


def describe_email_problem(email: str) -> str | None:
    """Return why `email` is unusable, or None if it looks like an address."""
    if len(email) > 254:
        return "Email address is too long."
    if not _EMAIL_SHAPE.match(email):
        return "That does not look like an email address."
    return None


class _EmailPayload(SQLModel):
    """Shared normalisation, so no route can forget to lowercase an address.

    The unique index is what ultimately enforces one-account-per-address; this
    makes sure the value reaching it has already been folded.
    """

    email: str = Field(min_length=3, max_length=254)

    @field_validator("email")
    @classmethod
    def _normalise(cls, value: str) -> str:
        folded = value.strip().lower()
        problem = describe_email_problem(folded)
        if problem:
            raise ValueError(problem)
        return folded


class RegisterRequest(_EmailPayload):
    password: str = Field(min_length=1, max_length=256)


class LoginRequest(_EmailPayload):
    password: str = Field(min_length=1, max_length=256)


class PasswordChangeRequest(SQLModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


class AccountRead(SQLModel):
    """What the account routes return. Never the password hash.

    `tier` is the *effective* tier, so a lapsed subscription reports `free` here
    and the frontend needs no expiry arithmetic of its own. `tier_expires_at` is
    still included, because "Pro until 3 March" is worth showing.

    `csrf_token` travels in the body rather than being read from the cookie: the
    cookie is host-only to the API, and if the frontend is served from a
    different subdomain then `document.cookie` there cannot see it. Returning it
    is safe because CORS stops a non-allowlisted origin from reading any
    response body.
    """

    email: str
    tier: Tier
    tier_expires_at: datetime | None = None
    trial_used: bool = False
    email_verified: bool = False
    last_login_at: datetime | None = None
    csrf_token: str

    @classmethod
    def of(cls, account: StatPitchAccount, csrf_token: str) -> "AccountRead":
        return cls(
            email=account.email,
            tier=account.effective_tier,
            tier_expires_at=account.tier_expires_at,
            trial_used=account.trial_used_at is not None,
            email_verified=account.email_verified_at is not None,
            last_login_at=account.last_login_at,
            csrf_token=csrf_token,
        )
