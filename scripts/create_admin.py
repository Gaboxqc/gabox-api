"""Create or update the admin account.

Deliberately a command-line tool rather than an API endpoint: the account only
ever needs creating once, and a registration route would be permanent attack
surface for a one-off job.

    python -m scripts.create_admin                    # create, prompting
    python -m scripts.create_admin --reset            # change the password
    python -m scripts.create_admin --deactivate       # disable the account

Run `alembic upgrade head` first so the tables exist.
"""

import argparse
import getpass
import sys

from sqlmodel import Session, select

from api.core.auth.models import AdminUser
from api.core.auth.passwords import describe_password_problem, hash_password
from api.core.auth.sessions import revoke_all_sessions
from api.core.database import engine


def _read_password(from_stdin: bool) -> str:
    """Obtain a password, rejecting anything unacceptable.

    `--password-stdin` exists because `getpass` on Windows reads the console
    directly and ignores piped input, which makes the interactive path
    impossible to script or test. Reading from stdin keeps the secret out of
    the process list, unlike passing it as an argument.
    """
    if from_stdin:
        password = sys.stdin.readline().rstrip("\n")
        problem = describe_password_problem(password)
        if problem:
            print(problem, file=sys.stderr)
            raise SystemExit(1)
        return password

    while True:
        password = getpass.getpass("Password: ")
        problem = describe_password_problem(password)
        if problem:
            print(f"  {problem}", file=sys.stderr)
            continue
        if password != getpass.getpass("Confirm password: "):
            print("  Passwords did not match.", file=sys.stderr)
            continue
        return password


def _find(db: Session, username: str) -> AdminUser | None:
    return db.exec(select(AdminUser).where(AdminUser.username == username)).first()


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage the admin account.")
    parser.add_argument("--username", help="Admin username (prompted when omitted).")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Set a new password for an existing account and log out every session.",
    )
    parser.add_argument(
        "--password-stdin",
        action="store_true",
        help="Read the password from stdin instead of prompting (for automation).",
    )
    parser.add_argument(
        "--deactivate",
        action="store_true",
        help="Disable the account and revoke its sessions, without deleting it.",
    )
    args = parser.parse_args()

    username = (args.username or input("Username: ")).strip()
    if len(username) < 3:
        print("Username must be at least 3 characters.", file=sys.stderr)
        return 1

    with Session(engine) as db:
        existing = _find(db, username)

        if args.deactivate:
            if existing is None:
                print(f"No admin named {username!r}.", file=sys.stderr)
                return 1
            existing.is_active = False
            db.add(existing)
            db.commit()
            closed = revoke_all_sessions(db, existing.id)
            print(f"Deactivated {username!r} and revoked {closed} session(s).")
            return 0

        if existing is not None and not args.reset:
            print(
                f"Admin {username!r} already exists. Pass --reset to change the password.",
                file=sys.stderr,
            )
            return 1

        password = _read_password(args.password_stdin)

        if existing is not None:
            existing.password_hash = hash_password(password)
            existing.is_active = True
            db.add(existing)
            db.commit()
            # A password change must invalidate sessions opened with the old one,
            # otherwise resetting after a suspected compromise achieves nothing.
            closed = revoke_all_sessions(db, existing.id)
            print(f"Password updated for {username!r}; revoked {closed} session(s).")
            return 0

        db.add(AdminUser(username=username, password_hash=hash_password(password)))
        db.commit()
        print(f"Created admin {username!r}.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
