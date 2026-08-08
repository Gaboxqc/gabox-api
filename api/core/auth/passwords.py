"""Password hashing.

argon2id is the current OWASP recommendation: unlike PBKDF2 or bcrypt it is
memory-hard, which is what makes GPU and ASIC cracking expensive rather than
merely slower.
"""

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

# 64 MiB / 3 passes lands around 50-100 ms on Vercel's runtime — slow enough to
# make offline cracking costly, fast enough that a login is not noticeably
# delayed. Parallelism 2 rather than the default 4 because serverless functions
# are allocated few cores and extra lanes just add contention.
_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)

# Verified when the username does not exist, purely to burn the same time a real
# verification would. Without this, "unknown user" returns measurably faster
# than "wrong password" and the endpoint becomes a user-enumeration oracle.
_DUMMY_HASH = _hasher.hash("timing-equalisation-placeholder")

MIN_PASSWORD_LENGTH = 12


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> tuple[bool, str | None]:
    """Return `(matched, rehashed)`.

    `rehashed` is a fresh hash when the stored one used weaker parameters than
    the current settings, so passwords are upgraded transparently on login. It
    is `None` when no upgrade is needed.
    """
    try:
        _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False, None

    if _hasher.check_needs_rehash(password_hash):
        return True, _hasher.hash(password)
    return True, None


def waste_time_like_a_verification() -> None:
    """Spend roughly one verification's worth of CPU on a throwaway hash."""
    try:
        _hasher.verify(_DUMMY_HASH, "definitely-not-the-placeholder")
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        pass


def describe_password_problem(password: str) -> str | None:
    """Return why `password` is unacceptable, or None if it is fine.

    Length is the only rule enforced. Composition rules ("one digit, one
    symbol") push people towards `Password1!` and are no longer recommended;
    length is what actually costs an attacker.
    """
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
    if password.strip() != password:
        return "Password must not start or end with whitespace."
    return None
