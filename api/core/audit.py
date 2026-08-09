"""Audit trail for admin writes.

Answers "what changed, when, and under which credential" — the question you
actually have after something unexpected appears on the site.

Deliberately records only the request line and its outcome, never bodies or
query strings. A body would eventually contain a password, and no amount of
care around one endpoint keeps that true as endpoints are added.
"""

import logging
from datetime import datetime

from sqlmodel import Field, Session, SQLModel
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from api.core.auth.models import utcnow
from api.core.database import engine

log = logging.getLogger("api.audit")


def audit_session() -> Session:
    """Session for writing audit rows.

    Middleware resumes after the request's own session has closed, so it needs
    its own. That means a second, short-lived connection per successful write —
    acceptable because writes are rare, and unavoidable without knowing the
    response status before the handler returns.

    Exposed as a module-level function rather than inlined so tests can point it
    at their own engine; the middleware bypasses dependency overrides by design.
    """
    return Session(engine)


_UNSAFE_METHODS = frozenset({"POST", "PATCH", "PUT", "DELETE"})

# Login and logout are already recorded in admin_login_attempt and
# admin_session; auditing them here would duplicate that and put the login
# endpoint one mistake away from logging a credential.
_SKIP_PREFIXES = ("/auth",)


class AuditLogEntry(SQLModel, table=True):
    __tablename__: str = "admin_audit_log"

    id: int | None = Field(default=None, primary_key=True)
    at: datetime = Field(default_factory=utcnow, index=True)
    method: str = Field(max_length=10)
    path: str = Field(max_length=512, index=True)
    status_code: int
    # "session" or "api_key" — which credential authorised the change.
    principal_kind: str = Field(max_length=20)
    # Null for master-key callers, who are not a person.
    username: str | None = Field(default=None, max_length=64, index=True)
    ip_address: str | None = Field(default=None, max_length=45)


def _should_audit(request: Request, status_code: int) -> bool:
    if request.method.upper() not in _UNSAFE_METHODS:
        return False
    if request.url.path.startswith(_SKIP_PREFIXES):
        return False
    # Only successful writes. A rejected request changed nothing, and logging
    # every failed probe would turn this table into an attack amplifier.
    return 200 <= status_code < 300


class AuditMiddleware(BaseHTTPMiddleware):
    """Records successful admin writes after the handler has run.

    It has to be middleware rather than a dependency because the outcome is only
    known once the response exists — a dependency runs too early to see it.
    """

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)

        if not _should_audit(request, response.status_code):
            return response

        # Set by require_admin. Absent means the route was public, so there is
        # no principal to attribute the change to.
        principal = getattr(request.state, "principal", None)
        if principal is None:
            return response

        try:
            with audit_session() as db:
                db.add(
                    AuditLogEntry(
                        method=request.method.upper(),
                        path=request.url.path[:512],
                        status_code=response.status_code,
                        principal_kind=principal.kind,
                        username=principal.username,
                        ip_address=request.client.host if request.client else None,
                    )
                )
                db.commit()
        except Exception:
            # An audit failure must never turn a successful write into an error
            # for the caller; the change already happened.
            log.exception("Failed to write audit entry for %s %s", request.method, request.url.path)

        return response
