"""Administrative routes for StatPitch customer accounts.

Separate from `api.statpitch.accounts`, which is the customer-facing side. The
split is the point: everything under `/statpitch/accounts` authenticates as a
customer, everything here authenticates as an admin, and no route belongs to
both.
"""

from api.statpitch.admin.router import router as admin_router

__all__ = ["admin_router"]
