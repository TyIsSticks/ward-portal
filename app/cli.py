"""User management from the command line.

    python -m app.cli create-user <username> [--admin | --leader]
    python -m app.cli set-password <username>
    python -m app.cli list-users
"""
import argparse
import getpass
import sqlite3
import sys

from . import auth, db


def _prompt_password() -> str:
    while True:
        try:
            return auth.validate_password(getpass.getpass("Password: "), getpass.getpass("Confirm:  "))
        except ValueError as exc:
            print(exc)


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    create = sub.add_parser("create-user")
    create.add_argument("username")
    create.add_argument("--admin", action="store_true")
    create.add_argument("--leader", action="store_true")
    setpw = sub.add_parser("set-password")
    setpw.add_argument("username")
    sub.add_parser("list-users")
    args = parser.parse_args()

    db.init()

    if args.cmd == "create-user":
        try:
            auth.validate_username(args.username)
        except ValueError as exc:
            print(exc)
            return 1
        try:
            role = "admin" if args.admin else "leader" if args.leader else "member"
            auth.create_user(args.username, _prompt_password(), role=role)
        except sqlite3.IntegrityError:
            print(f"User '{args.username}' already exists.")
            return 1
        print(f"Created {role} '{args.username}'.")
    elif args.cmd == "set-password":
        if not auth.set_password(args.username, _prompt_password()):
            print(f"No user '{args.username}'.")
            return 1
        print("Password updated.")
    elif args.cmd == "list-users":
        with db.connect() as conn:
            for row in conn.execute("SELECT username, is_admin, is_leader, created_at FROM users ORDER BY username"):
                print(f"{row['username']:<20} {auth.role_of(row):<7} {row['created_at']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
