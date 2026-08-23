"""Administrative routes for StatPitch customer accounts.

Separate from `api.statpitch.accounts`, which is the customer-facing side. The
split is the point: everything under `/statpitch/accounts` authenticates as a
customer, everything here authenticates as an admin, and no route belongs to
both.

Deliberately empty of imports. A package `__init__` that pulls in its own router
means importing *any* module from this package drags the whole administrative
surface along — which is how `trials` importing `grants` ended up loading the
admin router, and the router importing `trials` back. Import the submodule you
want.
"""
