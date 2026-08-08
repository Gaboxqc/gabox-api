"""Admin authentication: password login backed by server-side sessions.

The API-key auth in `api.core.security` stays as-is for machine callers. This
package adds a second, browser-safe path so the admin dashboard never has to
hold the master key.
"""

from api.core.auth.router import router as auth_router

__all__ = ["auth_router"]
