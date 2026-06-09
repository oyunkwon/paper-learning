"""Authentication: Google OAuth (authlib) + signed session cookie + email
whitelist. Multi-user is enforced here.

Flow:
  GET  /api/auth/login     -> redirect to Google consent
  GET  /api/auth/callback  -> exchange code, check whitelist, upsert user,
                              set session cookie, redirect to the SPA
  POST /api/auth/logout    -> clear session
  GET  /api/auth/me        -> current user (or 401)

The session cookie (Starlette SessionMiddleware, signed with SESSION_SECRET)
carries only the user's uuid. Every protected route depends on ``current_user``.

Dev bypass: when AUTH_DEV_BYPASS=1 and no Google client is configured,
``current_user`` resolves to the seeded DEFAULT_USER so local dev needs no
Google round-trip. This is force-disabled once a real client id is present.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from authlib.integrations.starlette_client import OAuth, OAuthError
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import select

from app.config import AuthSettings, load_auth_settings
from app.db import session_scope
from app.models import DEFAULT_USER_EMAIL, DEFAULT_USER_ID, AllowedEmail, User

log = logging.getLogger("tutor.auth")

router = APIRouter(prefix="/api/auth", tags=["auth"])

_GOOGLE_METADATA = "https://accounts.google.com/.well-known/openid-configuration"

# Lazily-built OAuth client (needs settings at startup).
_oauth: OAuth | None = None


def _settings() -> AuthSettings:
    return load_auth_settings()


def get_oauth() -> OAuth:
    global _oauth
    if _oauth is None:
        s = _settings()
        oauth = OAuth()
        oauth.register(
            name="google",
            client_id=s.google_client_id,
            client_secret=s.google_client_secret,
            server_metadata_url=_GOOGLE_METADATA,
            client_kwargs={"scope": "openid email profile"},
        )
        _oauth = oauth
    return _oauth


# ----- whitelist + user upsert ------------------------------------------


async def _is_allowed(email: str) -> bool:
    async with session_scope() as db:
        row = await db.get(AllowedEmail, email.lower())
        return row is not None


async def _upsert_user(*, email: str, name: str | None, picture: str | None) -> uuid.UUID:
    async with session_scope() as db:
        result = await db.execute(select(User).where(User.email == email.lower()))
        user = result.scalar_one_or_none()
        if user is None:
            user = User(
                id=uuid.uuid4(),
                email=email.lower(),
                name=name,
                picture=picture,
            )
            db.add(user)
            await db.flush()
        else:
            user.name = name or user.name
            user.picture = picture or user.picture
        user.last_login_at = datetime.now(timezone.utc)
        return user.id


# ----- current-user dependency ------------------------------------------


async def current_user(request: Request) -> User:
    """Resolve the authenticated user, or 401. Honors dev bypass."""
    s = _settings()
    if s.dev_bypass:
        async with session_scope() as db:
            user = await db.get(User, DEFAULT_USER_ID)
            if user is not None:
                return user
        # Bypass requested but seed missing — surface a clear error.
        raise HTTPException(status_code=500, detail="dev bypass: DEFAULT_USER not seeded")

    uid = request.session.get("uid")
    if not uid:
        raise HTTPException(status_code=401, detail="로그인이 필요합니다.")
    try:
        uid_u = uuid.UUID(uid)
    except (ValueError, TypeError):
        raise HTTPException(status_code=401, detail="세션이 유효하지 않습니다.")
    async with session_scope() as db:
        user = await db.get(User, uid_u)
    if user is None:
        request.session.pop("uid", None)
        raise HTTPException(status_code=401, detail="사용자를 찾을 수 없습니다.")
    return user


# ----- routes -----------------------------------------------------------


@router.get("/login")
async def login(request: Request):
    s = _settings()
    if not s.google_configured:
        return JSONResponse(
            status_code=503,
            content={"error": {"message": "Google 로그인이 설정되지 않았습니다."}},
        )
    redirect_uri = s.redirect_uri
    return await get_oauth().google.authorize_redirect(request, redirect_uri)


@router.get("/callback")
async def callback(request: Request):
    s = _settings()
    try:
        token = await get_oauth().google.authorize_access_token(request)
    except OAuthError as e:
        log.warning("oauth callback failed: %s", e)
        return RedirectResponse(url="/?auth_error=oauth")

    userinfo = token.get("userinfo") or {}
    email = (userinfo.get("email") or "").lower()
    if not email or not userinfo.get("email_verified", True):
        return RedirectResponse(url="/?auth_error=email")

    if not await _is_allowed(email):
        log.info("rejected non-whitelisted login: %s", email)
        return RedirectResponse(url="/?auth_error=not_allowed")

    uid = await _upsert_user(
        email=email,
        name=userinfo.get("name"),
        picture=userinfo.get("picture"),
    )
    request.session["uid"] = str(uid)
    log.info("login ok: %s", email)
    return RedirectResponse(url=s.post_login_redirect)


@router.post("/logout")
async def logout(request: Request):
    request.session.pop("uid", None)
    return {"ok": True}


@router.get("/me")
async def me(user: User = Depends(current_user)):
    return {
        "id": str(user.id),
        "email": user.email,
        "name": user.name,
        "picture": user.picture,
    }
