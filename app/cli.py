"""User management from the command line.

    python -m app.cli create-user <username> [--admin]
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
        pw = getpass.getpass("Password: ")
        if len(pw) < 10:
            print("Use at least 10 characters.")
            continue
        if pw != getpass.getpass("Confirm:  "):
            print("Passwords didn't match.")
            continue
        return pw


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="cmd", required=True)
    create = sub.add_parser("create-user")
    create.add_argument("username")
    create.add_argument("--admin", action="store_true")
    setpw = sub.add_parser("set-password")
    setpw.add_argument("username")
    sub.add_parser("list-users")
    args = parser.parse_args()

    db.init()

    if args.cmd == "create-user":
        try:
            auth.create_user(args.username, _prompt_password(), is_admin=args.admin)
        except sqlite3.IntegrityError:
            print(f"User '{args.username}' already exists.")
            return 1
        print(f"Created {'admin ' if args.admin else ''}user '{args.username}'.")
    elif args.cmd == "set-password":
        if not auth.set_password(args.username, _prompt_password()):
            print(f"No user '{args.username}'.")
            return 1
        print("Password updated.")
    elif args.cmd == "list-users":
        with db.connect() as conn:
            for row in conn.execute("SELECT username, is_admin, created_at FROM users ORDER BY username"):
                print(f"{row['username']:<20} {'admin' if row['is_admin'] else 'user':<6} {row['created_at']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
