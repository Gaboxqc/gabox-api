"""Deprecated — import from `api.core.security` instead.

Kept only so `api/statpitch/` keeps working untouched. Delete this module
once StatPitch is migrated to the core layer.
"""

from api.core.security import API_KEY_NAME, validate_api_key  # noqa: F401
