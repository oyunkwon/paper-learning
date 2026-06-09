"""Configuration: relay (OpenAI-compatible) LLM credentials + retrieval settings.

Resolution order for the API key (same as `learning`):
  1. RELAY_API_KEY env var
  2. ~/.local/share/opencode/auth.json -> relay.key

Kept config-light: no R2/auth here yet (this is a local single-user app at this
stage). Retrieval settings carry the contact email (OpenAlex polite pool) and
the on-disk cache dir for the retrieval layer.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_BASE_URL = "https://relay.algorix.io/v1"
DEFAULT_MODEL = "claude-opus-4.8"

_AUTH_PATH = Path(
    os.environ.get(
        "OPENCODE_AUTH_PATH",
        Path.home() / ".local" / "share" / "opencode" / "auth.json",
    )
)

# Local data dir for material binaries + retrieval cache. In Docker the compose
# file mounts a persistent volume at /data and sets TUTOR_DATA_DIR=/data.
DEFAULT_DATA_DIR = Path(
    os.environ.get("TUTOR_DATA_DIR")
    or os.environ.get("PAPER_DATA_DIR")
    or str(Path.home() / ".local" / "share" / "paper-learning")
)


class ConfigError(RuntimeError):
    """Raised when required provider configuration is missing."""


def _key_from_auth_file() -> str | None:
    try:
        if not _AUTH_PATH.exists() or _AUTH_PATH.stat().st_size == 0:
            return None
        data = json.loads(_AUTH_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    relay = data.get("relay") if isinstance(data, dict) else None
    if isinstance(relay, dict):
        key = relay.get("key")
        if isinstance(key, str) and key:
            return key
    return None


@dataclass(frozen=True)
class Settings:
    api_key: str
    base_url: str
    # Model used for both the planning passes and the tutoring loop. Must be
    # multimodal-capable (material pages are sent as images).
    planner_model: str
    tutor_model: str
    request_timeout_s: float = 180.0
    max_retries: int = 3

    @property
    def has_key(self) -> bool:
        return bool(self.api_key)


@dataclass(frozen=True)
class RetrievalSettings:
    """External retrieval (Semantic Scholar / OpenAlex / arXiv)."""

    contact_email: str
    cache_dir: Path
    timeout_s: float = 30.0
    max_retries: int = 3


@dataclass(frozen=True)
class StorageSettings:
    """Cloudflare R2 (S3-compatible) object storage config.

    When ``bucket`` is empty the app runs *local-only*: binaries live on the
    cache disk and are never mirrored to R2. Production sets the R2_* envs.
    """

    endpoint_url: str
    access_key_id: str
    secret_access_key: str
    bucket: str
    region: str = "auto"

    @property
    def enabled(self) -> bool:
        return bool(self.bucket and self.endpoint_url and self.access_key_id)


@dataclass(frozen=True)
class AuthSettings:
    """Google OAuth + session-cookie config.

    ``dev_bypass`` (AUTH_DEV_BYPASS=1) skips Google entirely and treats every
    request as the seeded DEFAULT_USER — local dev only. It is force-disabled
    whenever a real Google client id is configured, so it can never weaken a
    production deploy.
    """

    google_client_id: str
    google_client_secret: str
    redirect_uri: str
    session_secret: str
    post_login_redirect: str
    dev_bypass: bool

    @property
    def google_configured(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret)

    @property
    def cookie_secure(self) -> bool:
        """https-only cookie when the redirect URI is https (prod), while local
        http://localhost OAuth testing still works."""
        return self.redirect_uri.lower().startswith("https://")


@dataclass(frozen=True)
class DBSettings:
    """Async SQLAlchemy URL (postgresql+asyncpg://...)."""

    url: str


def load_db_settings() -> DBSettings:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        host = os.environ.get("PGHOST", "127.0.0.1")
        port = os.environ.get("PGPORT", "5432")
        user = os.environ.get("PGUSER", "postgres")
        pw = os.environ.get("PGPASSWORD", "postgres")
        db = os.environ.get("PGDATABASE", "paper_learning")
        url = f"postgresql+asyncpg://{user}:{pw}@{host}:{port}/{db}"
    return DBSettings(url=_as_asyncpg_url(url))


def _as_asyncpg_url(url: str) -> str:
    if url.startswith(("postgresql+asyncpg://", "postgresql+psycopg://")):
        return url
    if url.startswith("postgresql://"):
        return "postgresql+asyncpg://" + url[len("postgresql://") :]
    if url.startswith("postgres://"):
        return "postgresql+asyncpg://" + url[len("postgres://") :]
    return url


def load_retrieval_settings() -> RetrievalSettings:
    return RetrievalSettings(
        contact_email=os.environ.get("CONTACT_EMAIL", "paper-learning@localhost").strip(),
        cache_dir=DEFAULT_DATA_DIR / "retrieval-cache",
    )


def load_storage_settings() -> StorageSettings:
    return StorageSettings(
        endpoint_url=os.environ.get("R2_ENDPOINT_URL", "").strip(),
        access_key_id=os.environ.get("R2_ACCESS_KEY_ID", "").strip(),
        secret_access_key=os.environ.get("R2_SECRET_ACCESS_KEY", "").strip(),
        bucket=os.environ.get("R2_BUCKET", "").strip(),
        region=os.environ.get("R2_REGION", "auto").strip() or "auto",
    )


def _bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def load_auth_settings() -> AuthSettings:
    client_id = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
    # Dev bypass is only honored when Google is NOT configured — a real client id
    # means "this is a real deploy", so never bypass there.
    dev_bypass = _bool("AUTH_DEV_BYPASS") and not bool(client_id)
    return AuthSettings(
        google_client_id=client_id,
        google_client_secret=client_secret,
        redirect_uri=os.environ.get(
            "OAUTH_REDIRECT_URI", "http://localhost:5173/api/auth/callback"
        ).strip(),
        session_secret=os.environ.get(
            "SESSION_SECRET", "dev-insecure-secret-change-me"
        ).strip(),
        post_login_redirect=os.environ.get("POST_LOGIN_REDIRECT", "/").strip() or "/",
        dev_bypass=dev_bypass,
    )


def load_settings(*, require_key: bool = True) -> Settings:
    api_key = os.environ.get("RELAY_API_KEY") or _key_from_auth_file() or ""
    if require_key and not api_key:
        raise ConfigError(
            "No relay API key found. Set RELAY_API_KEY or run the opencode relay "
            "setup so the key exists in ~/.local/share/opencode/auth.json."
        )

    def _float(name: str, default: float) -> float:
        raw = os.environ.get(name)
        try:
            return float(raw) if raw is not None else default
        except ValueError:
            return default

    def _int(name: str, default: int) -> int:
        raw = os.environ.get(name)
        try:
            return int(raw) if raw is not None else default
        except ValueError:
            return default

    base_model = os.environ.get("LLM_MODEL", DEFAULT_MODEL)
    return Settings(
        api_key=api_key,
        base_url=os.environ.get("RELAY_BASE_URL", DEFAULT_BASE_URL),
        planner_model=os.environ.get("PLANNER_MODEL", base_model),
        tutor_model=os.environ.get("TUTOR_MODEL", base_model),
        request_timeout_s=_float("LLM_TIMEOUT_S", 180.0),
        max_retries=_int("LLM_MAX_RETRIES", 3),
    )
