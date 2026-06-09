"""Async SQLAlchemy engine + session factory.

learning에서 거의 그대로 차용. 프로세스당 엔진 하나를 ``DATABASE_URL``
(``app.config.load_db_settings`` 참고)에서 lazy 생성한다. 스토어 레이어
(``app.sessions``)는 작업마다 :func:`session_scope`로 짧게 사는
``AsyncSession``을 연다 — "모든 호출은 자기완결적"이라는 옛 파일스토어 원칙과 동형.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import load_db_settings

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        url = load_db_settings().url
        _engine = create_async_engine(url, pool_pre_ping=True, future=True)
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            get_engine(), expire_on_commit=False, autoflush=False
        )
    return _sessionmaker


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """트랜잭션 스코프: 성공 시 commit, 에러 시 rollback, 항상 close."""
    maker = get_sessionmaker()
    async with maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def dispose_engine() -> None:
    """엔진 정리 (앱 종료 시 호출)."""
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None
