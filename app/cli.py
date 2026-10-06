"""Accounts and wards from the command line.

    python -m app.cli create-user <username> [--admin | --ward-admin | --leader] [--ward ID]
    python -m app.cli set-password <username>
    python -m app.cli list-users
    python -m app.cli create-ward "<name>"
    python -m app.cli list-wards
"""
import argparse
import getpass
import sqlite3
import sys

from . import auth, db, wards


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
    roles = create.add_mutually_exclusive_group()
    roles.add_argument("--admin", action="store_true", help="admin of every ward")
    roles.add_argument("--ward-admin", action="store_true", help="manages accounts in their ward")
    roles.add_argument("--leader", action="store_true")
    create.add_argument("--ward", type=int, help="ward id (see list-wards); defaults to the first ward")
    setpw = sub.add_parser("set-password")
    setpw.add_argument("username")
    sub.add_parser("list-users")
    cw = sub.add_parser("create-ward")
    cw.add_argument("name")
    sub.add_parser("list-wards")
    args = parser.parse_args()

    db.init()

    if args.cmd == "create-user":
        try:
            auth.validate_username(args.username)
        except ValueError as exc:
            print(exc)
            return 1
        if args.ward:
            with db.connect() as conn:
                if not wards.get(conn, args.ward):
                    print(f"No ward with id {args.ward}. See: python -m app.cli list-wards")
                    return 1
        role = ("admin" if args.admin else "ward_admin" if args.ward_admin
                else "leader" if args.leader else "member")
        try:
            auth.create_user(args.username, _prompt_password(), role=role, ward_id=args.ward)
        except sqlite3.IntegrityError:
            print(f"User '{args.username}' already exists.")
            return 1
        print(f"Created {auth.ROLE_LABELS[role].lower()} '{args.username}'.")
    elif args.cmd == "set-password":
        if not auth.set_password(args.username, _prompt_password()):
            print(f"No user '{args.username}'.")
            return 1
        print("Password updated.")
    elif args.cmd == "list-users":
        with db.connect() as conn:
            for row in conn.execute(
                    "SELECT u.*, w.name AS ward FROM users u LEFT JOIN wards w ON w.id = u.ward_id "
                    "ORDER BY w.name, u.username"):
                print(f"{row['username']:<20} {auth.role_of(row):<11} {row['ward'] or '-':<30} {row['created_at']}")
    elif args.cmd == "create-ward":
        with db.connect() as conn:
            ward_id = wards.create(conn, args.name)
        print(f"Created ward {ward_id}: {args.name}")
    elif args.cmd == "list-wards":
        with db.connect() as conn:
            for w in wards.list_wards(conn):
                print(f"{w['id']:>3}  {w['name']:<40} {w['users']} users")
    return 0


if __name__ == "__main__":
    sys.exit(main())
