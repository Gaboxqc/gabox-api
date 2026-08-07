"""Deprecated — import from `api.core.database` instead.

Kept only so `api/statpitch/` keeps working untouched. Delete this module
once StatPitch is migrated to the core layer.
"""

from api.core.database import SessionDep, engine, get_session  # noqa: F401
from sqlmodel import SQLModel  # noqa: F401
