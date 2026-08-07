"""Global exception handlers.

Database constraint violations are a client mistake, not a server fault.
Without these handlers SQLAlchemy's IntegrityError escapes as a 500 and the
caller has no way to tell a duplicate name from a genuine outage.

Responses keep FastAPI's `{"detail": ...}` shape so clients need no changes.
"""

import logging

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError

log = logging.getLogger("api.errors")

# PostgreSQL SQLSTATE codes (see appendix A of the Postgres manual).
_UNIQUE_VIOLATION = "23505"
_FOREIGN_KEY_VIOLATION = "23503"
_NOT_NULL_VIOLATION = "23502"

# SQLite reports the same conditions as message text only.
_SQLITE_MARKERS = {
    "UNIQUE constraint failed": _UNIQUE_VIOLATION,
    "FOREIGN KEY constraint failed": _FOREIGN_KEY_VIOLATION,
    "NOT NULL constraint failed": _NOT_NULL_VIOLATION,
}


def _sqlstate(exc: IntegrityError) -> str | None:
    origin = getattr(exc, "orig", None)
    code = getattr(origin, "pgcode", None)
    if code:
        return code
    text = str(origin or exc)
    return next((c for marker, c in _SQLITE_MARKERS.items() if marker in text), None)


def _constraint_name(exc: IntegrityError) -> str | None:
    diagnostics = getattr(getattr(exc, "orig", None), "diag", None)
    return getattr(diagnostics, "constraint_name", None)


async def integrity_error_handler(request: Request, exc: IntegrityError) -> JSONResponse:
    code = _sqlstate(exc)
    constraint = _constraint_name(exc)

    if code == _UNIQUE_VIOLATION:
        status_code = status.HTTP_409_CONFLICT
        detail = "A record with these values already exists."
        if constraint:
            detail = f"{detail} Violated constraint: {constraint}."
    elif code == _FOREIGN_KEY_VIOLATION:
        # Same SQLSTATE, opposite meaning depending on direction: writing a row
        # that points at something missing is a bad payload (422); deleting a
        # row something else still points at is a state conflict (409).
        if request.method == "DELETE":
            status_code = status.HTTP_409_CONFLICT
            detail = "This record is still referenced by other records and cannot be deleted."
        else:
            status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
            detail = "A referenced record does not exist."
        if constraint:
            detail = f"{detail} Violated constraint: {constraint}."
    elif code == _NOT_NULL_VIOLATION:
        status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
        detail = "A required field was missing."
    else:
        status_code = status.HTTP_409_CONFLICT
        detail = "The request conflicts with the current state of the database."

    # The raw error can carry column values, so it goes to the log, not the body.
    log.warning(
        "IntegrityError on %s %s (sqlstate=%s): %s",
        request.method,
        request.url.path,
        code,
        getattr(exc, "orig", exc),
    )
    return JSONResponse(status_code=status_code, content={"detail": detail})


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(IntegrityError, integrity_error_handler)
