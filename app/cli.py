"""Accounts and wards from the command line.

    python -m app.cli create-user <username> [--admin | --ward-admin | --leader | --teacher] [--ward ID]
    python -m app.cli set-password <username>
    python -m app.cli list-users
    python -m app.cli create-ward "<name>"
    python -m app.cli list-wards
    python -m app.cli backup
    python -m app.cli list-backups
    python -m app.cli restore-backup <file.zip> [--yes]     (stop the app first)
"""
import argparse
import getpass
import sqlite3
import sys

from pathlib import Path

from . import auth, backup, db, wards


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
    roles.add_argument("--teacher", action="store_true", help="can see the lesson plan, not change it")
    create.add_argument("--ward", type=int, help="ward id (see list-wards); defaults to the first ward")
    setpw = sub.add_parser("set-password")
    setpw.add_argument("username")
    sub.add_parser("list-users")
    cw = sub.add_parser("create-ward")
    cw.add_argument("name")
    sub.add_parser("list-wards")
    sub.add_parser("backup")
    sub.add_parser("list-backups")
    restore = sub.add_parser("restore-backup")
    restore.add_argument("file")
    restore.add_argument("--yes", action="store_true", help="don't ask before replacing the data")
    args = parser.parse_args()

    if args.cmd == "restore-backup":   # before db.init(): it shouldn't touch the data it's about to replace
        if not args.yes and input("This replaces ALL current data with the backup. The app must be stopped. "
                                  "Type 'yes' to go on: ").strip().lower() != "yes":
            print("Cancelled.")
            return 1
        try:
            files = backup.restore(Path(args.file))
        except backup.BackupError as exc:
            print(exc)
            return 1
        print(f"Restored {len(files)} database(s). A copy of the old data was saved as a 'pre-restore' backup.")
        return 0

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
                else "leader" if args.leader else "teacher" if args.teacher else "member")
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
    elif args.cmd == "backup":
        print(f"Wrote {backup.create('manual')}")
    elif args.cmd == "list-backups":
        for b in backup.listing():
            print(f"{b['name']:<36} {b['size'] / 1024:>9.0f} KB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
