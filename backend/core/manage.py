#!/usr/bin/env python3
"""
core/manage.py — account administration from the command line. There is no self-registration.

    python -m core.manage create-user --email a@b.edu --role analyst [--org "X College"]
    python -m core.manage set-role   --email a@b.edu --role admin
    python -m core.manage list-users
    python -m core.manage deactivate --email a@b.edu

The password is read interactively (never passed on the command line) and stored only as a hash.
"""

import argparse
import getpass
import sys

from core.auth import ROLES


def _db():
    from db.mysql import execute, query, query_one
    return execute, query, query_one


def create_user(email, role, org):
    from werkzeug.security import generate_password_hash
    execute, _, query_one = _db()
    email = email.strip().lower()
    if role not in ROLES:
        print(f"role must be one of {ROLES}", file=sys.stderr)
        return 2
    if query_one("SELECT id FROM users WHERE email = %s", (email,)):
        print(f"user {email} already exists", file=sys.stderr)
        return 1
    pw = getpass.getpass("Password: ")
    if len(pw) < 8:
        print("password must be at least 8 characters", file=sys.stderr)
        return 2
    if pw != getpass.getpass("Confirm password: "):
        print("passwords do not match", file=sys.stderr)
        return 2
    execute("INSERT INTO users (email, password_hash, role, organisation) VALUES (%s, %s, %s, %s)",
            (email, generate_password_hash(pw), role, org))
    print(f"created {email} ({role})")
    return 0


def set_role(email, role):
    execute, _, query_one = _db()
    if role not in ROLES:
        print(f"role must be one of {ROLES}", file=sys.stderr)
        return 2
    if not query_one("SELECT id FROM users WHERE email = %s", (email.strip().lower(),)):
        print(f"no such user: {email}", file=sys.stderr)
        return 1
    execute("UPDATE users SET role = %s WHERE email = %s", (role, email.strip().lower()))
    print(f"{email} -> {role}")
    return 0


def deactivate(email):
    execute, _, _ = _db()
    n = execute("UPDATE users SET active = 0 WHERE email = %s", (email.strip().lower(),))
    print(f"deactivated {email}" if n else f"no such user: {email}")
    return 0 if n else 1


def list_users():
    _, query, _ = _db()
    rows = query("SELECT email, role, organisation, active, last_login FROM users ORDER BY role, email")
    if not rows:
        print("no users")
        return 0
    for r in rows:
        flag = "" if r["active"] else " (inactive)"
        print(f"  {r['role']:8} {r['email']}{flag}  org={r.get('organisation') or '-'}  last_login={r.get('last_login') or '-'}")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create-user"); c.add_argument("--email", required=True); c.add_argument("--role", default="viewer"); c.add_argument("--org", default=None)
    s = sub.add_parser("set-role"); s.add_argument("--email", required=True); s.add_argument("--role", required=True)
    d = sub.add_parser("deactivate"); d.add_argument("--email", required=True)
    sub.add_parser("list-users")
    args = p.parse_args(argv)
    if args.cmd == "create-user":
        return create_user(args.email, args.role, args.org)
    if args.cmd == "set-role":
        return set_role(args.email, args.role)
    if args.cmd == "deactivate":
        return deactivate(args.email)
    if args.cmd == "list-users":
        return list_users()
    return 1


if __name__ == "__main__":
    sys.exit(main())
