"""Manage the email whitelist (allowed_emails) and inspect users.

The whitelist gates Google login: only listed emails can sign in. An empty
table means nobody can log in (safe default).

    uv run python -m app.admin allow you@gmail.com friend@gmail.com
    uv run python -m app.admin list
    uv run python -m app.admin revoke friend@gmail.com
    uv run python -m app.admin users
"""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import delete, select

from app.db import dispose_engine, session_scope
from app.models import AllowedEmail, User


async def _allow(emails: list[str]) -> None:
    async with session_scope() as db:
        for raw in emails:
            email = raw.strip().lower()
            if not email:
                continue
            existing = await db.get(AllowedEmail, email)
            if existing is None:
                db.add(AllowedEmail(email=email))
                print(f"+ allowed {email}")
            else:
                print(f"= already allowed {email}")


async def _revoke(emails: list[str]) -> None:
    async with session_scope() as db:
        for raw in emails:
            email = raw.strip().lower()
            await db.execute(delete(AllowedEmail).where(AllowedEmail.email == email))
            print(f"- revoked {email}")


async def _list() -> None:
    async with session_scope() as db:
        rows = (await db.execute(select(AllowedEmail).order_by(AllowedEmail.email))).scalars().all()
        if not rows:
            print("(whitelist empty — nobody can log in)")
            return
        for r in rows:
            print(r.email)


async def _users() -> None:
    async with session_scope() as db:
        rows = (await db.execute(select(User).order_by(User.created_at))).scalars().all()
        if not rows:
            print("(no users yet)")
            return
        for u in rows:
            last = u.last_login_at.isoformat() if u.last_login_at else "never"
            print(f"{u.email:40} {u.name or '':20} last_login={last}")


async def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    cmd, args = argv[0], argv[1:]
    try:
        if cmd == "allow" and args:
            await _allow(args)
        elif cmd == "revoke" and args:
            await _revoke(args)
        elif cmd == "list":
            await _list()
        elif cmd == "users":
            await _users()
        else:
            print(__doc__)
            return 2
        return 0
    finally:
        await dispose_engine()


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
