"""Tables backing admin login.

There is no `AdminUserCreate` schema and no registration route on purpose: the
only account is created from the command line (`scripts/create_admin.py`). A
public signup endpoint would be pure attack surface.

Every timestamp here is *naive UTC*. SQLite — which the test suite runs on —
cannot round-trip a timezone-aware datetime, so a tz-aware column would come
back naive and every `aware < naive` comparison would raise TypeError. Storing
naive UTC everywhere keeps the same code correct on both backends.
"""

from datetime import UTC, datetime

from sqlmodel import Field, Relationship, SQLModel


def utcnow() -> datetime:
    """Current UTC time, naive. See the module docstring for why."""
    return datetime.now(UTC).replace(tzinfo=None)


class AdminUser(SQLModel, table=True):
    __tablename__: str = "admin_user"

    id: int | None = Field(default=None, primary_key=True)
    username: str = Field(unique=True, index=True, min_length=3, max_length=64)
    # argon2id PHC string: algorithm, parameters and salt travel with the hash,
    # so the parameters can be raised later without invalidating old passwords.
    password_hash: str
    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=utcnow)
    last_login_at: datetime | None = Field(default=None)

    sessions: list["AdminSession"] = Relationship(back_populates="user", cascade_delete=True)


class AdminSession(SQLModel, table=True):
    """One row per active login.

    Server-side sessions rather than JWTs: a JWT cannot be revoked, so a stolen
    one stays valid until it expires. Deleting or revoking a row here logs that
    session out immediately, and the lookup is free because every request
    already talks to Postgres.
    """

    __tablename__: str = "admin_session"

    id: int | None = Field(default=None, primary_key=True)
    # Only the SHA-256 of the token is stored, so a database leak does not hand
    # over live sessions. SHA-256 rather than argon2 because the token is 256
    # bits of CSPRNG output — there is nothing to brute-force, and this runs on
    # every authenticated request.
    token_hash: str = Field(unique=True, index=True)
    # Compared against the X-CSRF-Token header on unsafe methods. Held server
    # side so this is a real check, not just cookie-vs-header agreement.
    csrf_token: str
    user_id: int = Field(foreign_key="admin_user.id", ondelete="CASCADE", index=True)
    created_at: datetime = Field(default_factory=utcnow)
    last_used_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime
    revoked_at: datetime | None = Field(default=None)
    ip_address: str | None = Field(default=None, max_length=45)
    user_agent: str | None = Field(default=None, max_length=256)

    user: AdminUser = Relationship(back_populates="sessions")


class LoginAttempt(SQLModel, table=True):
    """Audit trail for the lockout check.

    Kept in the database rather than in memory because the API runs on
    serverless functions: each invocation is a fresh process, so an in-process
    counter would reset continuously and enforce nothing.
    """

    __tablename__: str = "admin_login_attempt"

    id: int | None = Field(default=None, primary_key=True)
    username: str = Field(index=True, max_length=64)
    ip_address: str = Field(index=True, max_length=45)
    succeeded: bool = Field(default=False)
    attempted_at: datetime = Field(default_factory=utcnow, index=True)


class LoginRequest(SQLModel):
    """Login payload. Not a table."""

    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class AdminRead(SQLModel):
    """What `/auth/login` and `/auth/me` return. Excludes the password hash.

    `csrf_token` is delivered in the body rather than read from the readable
    cookie, because the cookie is host-only to the API and the dashboard runs on
    a different subdomain — `document.cookie` there cannot see it. Local
    development hides this, since localhost:5173 and localhost:8000 are the same
    host and do share cookies.

    Returning it is safe: CORS stops a non-allowlisted origin from reading any
    response body, so only the real dashboard can obtain it.
    """

    username: str
    last_login_at: datetime | None = None
    csrf_token: str
