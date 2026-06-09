"""SQLAlchemy ORM 모델.

멀티유저 + 인증을 위해 learning과 동형으로 ``User`` / ``AllowedEmail`` 테이블과
``SessionRow.user_id``를 둔다. 인증이 꺼진 로컬/dev에서는 모든 데이터가 시드된
``DEFAULT_USER`` 소유다(스키마 재작성 없이 멀티유저 전환 가능).

타입은 cross-dialect로 둔다: Postgres에선 JSONB/UUID, SQLite(테스트)에선 generic
JSON/CHAR로 떨어지도록 ``with_variant`` + 제너릭 ``Uuid``를 쓴다. 덕분에 스토어
단위 테스트가 Postgres 없이 aiosqlite로 돈다.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# Postgres에선 JSONB, 그 외(SQLite 등)에선 generic JSON으로 떨어진다.
JSONVariant = JSON().with_variant(JSONB(), "postgresql")

# 인증 전(또는 dev bypass) 단일 유저. 고정 UUID라 마이그레이션/이전 스크립트가
# 결정적으로 참조한다.
DEFAULT_USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
DEFAULT_USER_EMAIL = "local@localhost"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    email: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    name: Mapped[str | None] = mapped_column(Text)
    picture: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    sessions: Mapped[list["SessionRow"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class AllowedEmail(Base):
    """화이트리스트. 빈 테이블 => 아무도 못 들어옴(안전 기본값)."""

    __tablename__ = "allowed_emails"

    email: Mapped[str] = mapped_column(Text, primary_key=True)
    added_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )


class SessionRow(Base):
    """학습 세션: 메타 + 커리큘럼(JSONB). app.sessions의 데이터클래스 facade 및
    SQLAlchemy 자체 Session과의 이름 충돌을 피해 SessionRow로 명명."""

    __tablename__ = "sessions"
    __table_args__ = (
        CheckConstraint("kind in ('pdf','md')", name="ck_sessions_kind"),
        Index("ix_sessions_user_created", "user_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)
    page_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    curriculum: Mapped[dict | None] = mapped_column(JSONVariant)
    # 플래닝은 업로드 후 백그라운드로 돈다(큰 PDF의 요청 타임아웃 회피). status:
    # 커리큘럼이 만들어질 때까지 'planning', 그다음 'ready' (또는 'error' +
    # planning_error 메시지). progress_done/total이 실제 % 바를 구동한다.
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'ready'")
    )
    planning_error: Mapped[str | None] = mapped_column(Text)
    progress_done: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    progress_total: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )

    user: Mapped[User] = relationship(back_populates="sessions")
    threads: Mapped[list["Thread"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )


class Thread(Base):
    """(session, track, chapter)별 채팅 트랜스크립트. 채팅 append는 핫패스라
    sessions.curriculum에 접지 않고 별도 행으로 둔다(매 턴 세션 문서 전체를
    다시 쓰면 낭비 + 챕터 간 경합).

    learning은 (session, chapter)였으나 멀티트랙을 위해 **track_id를 PK에 추가**.
    이로써 C4의 2단계 중첩(trackId -> chapterId -> messages)을 DB에서 표현한다."""

    __tablename__ = "threads"

    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        primary_key=True,
    )
    track_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    chapter_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    messages: Mapped[list] = mapped_column(
        JSONVariant, nullable=False, server_default=text("'[]'")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now, onupdate=_now
    )

    session: Mapped[SessionRow] = relationship(back_populates="threads")
