"""StatPitch customer accounts: signup, login, and paid tiers.

Self-contained inside the StatPitch module. It borrows the argon2 configuration
from `api.core.auth.passwords` and nothing else — in particular it does not
touch the admin tables, and no route here can grant administrative access.

Importing this package registers its tables on the SQLModel metadata.
"""

from api.statpitch.accounts.models import (
    TIER_ORDER,
    StatPitchAccount,
    StatPitchAccountSession,
    StatPitchLoginAttempt,
    Tier,
    TierSource,
)

__all__ = [
    "TIER_ORDER",
    "StatPitchAccount",
    "StatPitchAccountSession",
    "StatPitchLoginAttempt",
    "Tier",
    "TierSource",
]
