"""Invite-only user seed CLI.

Usage (from backend/):
  python -m scripts.seed_user --email alice@example.com --password 'SecurePass1!'
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from app.core.config import get_settings
from app.core.security.password import validate_password_strength
from app.db.session import dispose_engine, get_session_factory
from app.services import auth_service


async def _run(email: str, password: str, role: str) -> int:
    validate_password_strength(password)
    get_settings()  # ensure env loaded
    factory = get_session_factory()
    async with factory() as session, session.begin():
        user = await auth_service.create_user(
            session,
            email=email,
            password=password,
            role=role,
        )
    print(f"Created user id={user.id} email={user.email} role={user.role}")
    await dispose_engine()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed an invite-only web user")
    parser.add_argument("--email", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--role", default="user", choices=["user", "admin"])
    args = parser.parse_args(argv)
    try:
        return asyncio.run(_run(args.email, args.password, args.role))
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
