"""세션 스토어: Postgres(메타 + 커리큘럼 + threads) over async SQLAlchemy.
바이너리(material 파일 + 렌더된 page jpg)는 R2 + 로컬 캐시(app.storage, "방식 2").

learning 스토어와 공개 모양을 가깝게 유지해 파이프라인(material.py / planner /
tutor.py)이 ``session.material_path`` / ``session.pages_dir``로 자료를 계속 읽게
한다 — 이 경로들은 R2 객체 키를 미러링하는 *로컬 캐시* 경로다.

멀티유저: 모든 세션은 ``user_id`` 소유다. ``get``은 ``owner_id``가 주어지면
소유자 검증을 하고, 비소유자에겐 None(=404)을 반환해 세션 존재 여부가 유저 간
새지 않게 한다. Thread는 (session, **track**, chapter) 단위 → all_threads가 C4
2단계 중첩 dict를 반환한다.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import select, update

from app import storage as storage_mod
from app.db import session_scope
from app.models import DEFAULT_USER_ID, SessionRow, Thread

Kind = Literal["pdf", "md"]


def _to_epoch(dt: datetime) -> float:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _parse_id(session_id: str) -> uuid.UUID | None:
    """관대한 id 파싱: dashed uuid 또는 32자 hex를 받는다."""
    try:
        return uuid.UUID(str(session_id))
    except (ValueError, AttributeError):
        return None


@dataclass
class Session:
    """세션 행의 순수 데이터 뷰. I/O 없음. 경로 프로퍼티는 로컬 캐시(R2 키 미러)를
    가리킨다."""

    id: str
    user_id: str
    title: str
    filename: str
    kind: Kind
    page_count: int
    created_at: float
    status: str = "ready"
    planning_error: str | None = None
    progress_done: int = 0
    progress_total: int = 0

    # ----- 로컬 캐시 경로 (R2 객체 키 미러) ------------------------------
    @property
    def _prefix(self) -> str:
        return storage_mod.session_prefix(self.user_id, self.id)

    @property
    def material_path(self) -> Path:
        return storage_mod.storage.cache_path(
            storage_mod.material_key(self.user_id, self.id, self.kind)
        )

    @property
    def pages_dir(self) -> Path:
        return storage_mod.storage.cache_path(self._prefix) / "pages"

    @property
    def material_key(self) -> str:
        return storage_mod.material_key(self.user_id, self.id, self.kind)

    def to_summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "filename": self.filename,
            "kind": self.kind,
            "createdAt": self.created_at,
            "status": self.status,
            "planningError": self.planning_error,
            "progress": {"done": self.progress_done, "total": self.progress_total},
        }


def _row_to_session(row: SessionRow) -> Session:
    return Session(
        id=str(row.id),
        user_id=str(row.user_id),
        title=row.title,
        filename=row.filename,
        kind=row.kind,  # type: ignore[arg-type]
        page_count=row.page_count,
        created_at=_to_epoch(row.created_at),
        status=row.status,
        planning_error=row.planning_error,
        progress_done=row.progress_done,
        progress_total=row.progress_total,
    )


class SessionStore:
    """async, DB 기반 세션 스토어. 바이너리는 R2 + 로컬 캐시(app.storage)."""

    def __init__(self, user_id: uuid.UUID = DEFAULT_USER_ID) -> None:
        # 인증 전(또는 dev bypass)엔 모든 세션이 시드 DEFAULT_USER 소유.
        self.default_user_id = user_id

    # ----- lifecycle ----------------------------------------------------
    async def create(
        self,
        *,
        filename: str,
        kind: Kind,
        title: str,
        user_id: uuid.UUID | None = None,
        status: str = "ready",
    ) -> Session:
        sid = uuid.uuid4()
        uid = user_id or self.default_user_id
        async with session_scope() as db:
            row = SessionRow(
                id=sid,
                user_id=uid,
                title=title,
                filename=filename,
                kind=kind,
                page_count=0,
                status=status,
            )
            db.add(row)
            await db.flush()
            return _row_to_session(row)

    async def get(
        self, session_id: str, *, owner_id: uuid.UUID | None = None
    ) -> Session | None:
        """세션 조회. ``owner_id``가 주어지면 소유자일 때만 반환(데이터 격리) —
        아니면 None이라 비소유자는 '없는 세션'과 구분되지 않는다."""
        sid = _parse_id(session_id)
        if sid is None:
            return None
        async with session_scope() as db:
            row = await db.get(SessionRow, sid)
            if row is None:
                return None
            if owner_id is not None and row.user_id != owner_id:
                return None
            return _row_to_session(row)

    async def list(self, user_id: uuid.UUID | None = None) -> list[Session]:
        uid = user_id or self.default_user_id
        async with session_scope() as db:
            result = await db.execute(
                select(SessionRow)
                .where(SessionRow.user_id == uid)
                .order_by(SessionRow.created_at.desc())
            )
            return [_row_to_session(r) for r in result.scalars().all()]

    async def delete(self, session_id: str) -> None:
        sid = _parse_id(session_id)
        if sid is None:
            return
        async with session_scope() as db:
            row = await db.get(SessionRow, sid)
            if row is None:
                return
            prefix = storage_mod.session_prefix(str(row.user_id), str(row.id))
            await db.delete(row)
        await storage_mod.storage.delete_prefix(prefix)

    # ----- planning state ----------------------------------------------
    async def set_progress(self, session_id: str, done: int, total: int) -> None:
        sid = _parse_id(session_id)
        if sid is None:
            return
        async with session_scope() as db:
            row = await db.get(SessionRow, sid)
            if row is not None:
                row.progress_done = done
                row.progress_total = total

    async def mark_ready(
        self, session_id: str, curriculum: dict[str, Any], *, title: str | None = None
    ) -> None:
        sid = _parse_id(session_id)
        if sid is None:
            return
        async with session_scope() as db:
            row = await db.get(SessionRow, sid)
            if row is None:
                return
            row.curriculum = curriculum
            row.status = "ready"
            row.planning_error = None
            if title is not None:
                row.title = title

    async def mark_error(self, session_id: str, message: str) -> None:
        sid = _parse_id(session_id)
        if sid is None:
            return
        async with session_scope() as db:
            row = await db.get(SessionRow, sid)
            if row is not None:
                row.status = "error"
                row.planning_error = message[:500]

    async def fail_stuck_planning(self, message: str) -> int:
        """아직 'planning'인 모든 세션을 error로 표시. 시작 시 호출한다: 백그라운드
        플래닝 task는 워커 메모리에만 사니, 재시작/재배포 후 진행 중이던 세션이
        영원히 'planning'에 갇힌다. 이를 error로 풀어 사용자가 재업로드하게 한다.
        정리한 세션 수를 반환."""
        async with session_scope() as db:
            result = await db.execute(
                update(SessionRow)
                .where(SessionRow.status == "planning")
                .values(status="error", planning_error=message[:500])
            )
            return result.rowcount or 0

    # ----- meta ---------------------------------------------------------
    async def set_title(self, session_id: str, title: str) -> Session | None:
        sid = _parse_id(session_id)
        if sid is None:
            return None
        async with session_scope() as db:
            row = await db.get(SessionRow, sid)
            if row is None:
                return None
            row.title = title
            await db.flush()
            return _row_to_session(row)

    # ----- material (바이너리, R2 + 로컬 캐시) --------------------------
    async def save_material(self, session: Session, data: bytes) -> None:
        """업로드된 자료를 로컬 캐시 + R2에 쓴다."""
        await storage_mod.storage.put_bytes(session.material_key, data)

    async def save_pages(self, session: Session, page_count: int) -> None:
        """렌더된 page jpg를 R2로 미러링하고 page_count를 기록한다. R2 비활성 시
        로컬 캐시에 이미 쓰여 있으므로 put_file은 사실상 no-op."""
        for idx in range(1, page_count + 1):
            local = session.pages_dir / f"page-{idx:03d}.jpg"
            if local.exists():
                key = storage_mod.page_key(session.user_id, session.id, idx)
                await storage_mod.storage.put_file(key, local)
        await self._set_page_count(session.id, page_count)
        session.page_count = page_count

    async def ensure_cached(self, session: Session) -> None:
        """material + page 이미지가 로컬 디스크에 있도록 보장(재배포로 캐시가
        비워졌으면 R2에서 당긴다). R2 비활성 + 이미 로컬이면 no-op."""
        await storage_mod.storage.ensure_cached(session.material_key)
        for idx in range(1, session.page_count + 1):
            await storage_mod.storage.ensure_cached(
                storage_mod.page_key(session.user_id, session.id, idx)
            )

    async def storage_ensure_material(self, session: Session) -> None:
        """material 파일만 R2 → 로컬 캐시로 rehydrate."""
        await storage_mod.storage.ensure_cached(session.material_key)

    async def _set_page_count(self, session_id: str, count: int) -> None:
        sid = _parse_id(session_id)
        if sid is None:
            return
        async with session_scope() as db:
            row = await db.get(SessionRow, sid)
            if row is not None:
                row.page_count = count

    # ----- curriculum ---------------------------------------------------
    async def read_curriculum(self, session_id: str) -> dict[str, Any] | None:
        sid = _parse_id(session_id)
        if sid is None:
            return None
        async with session_scope() as db:
            row = await db.get(SessionRow, sid)
            return row.curriculum if row is not None else None

    async def write_curriculum(
        self, session_id: str, curriculum: dict[str, Any], *, title: str | None = None
    ) -> None:
        sid = _parse_id(session_id)
        if sid is None:
            return
        async with session_scope() as db:
            row = await db.get(SessionRow, sid)
            if row is None:
                return
            row.curriculum = curriculum
            if title is not None:
                row.title = title

    # ----- threads (track 인식) -----------------------------------------
    async def read_thread(
        self, session_id: str, track_id: str, chapter_id: str
    ) -> list[dict[str, Any]]:
        sid = _parse_id(session_id)
        if sid is None:
            return []
        async with session_scope() as db:
            row = await db.get(Thread, (sid, track_id, chapter_id))
            return list(row.messages) if row is not None else []

    async def append_to_thread(
        self,
        session_id: str,
        track_id: str,
        chapter_id: str,
        role: str,
        content: str,
        *,
        synthetic: bool = False,
    ) -> dict[str, Any]:
        sid = _parse_id(session_id)
        entry: dict[str, Any] = {"role": role, "content": content, "ts": time.time()}
        if synthetic:
            # 시스템 주입 턴(챕터 kickoff, 개념 pass) — 튜터를 구동하지만 학습자에게
            # 본인 메시지로 보이면 안 된다.
            entry["synthetic"] = True
        if sid is None:
            return entry
        async with session_scope() as db:
            row = await db.get(Thread, (sid, track_id, chapter_id))
            if row is None:
                row = Thread(
                    session_id=sid,
                    track_id=track_id,
                    chapter_id=chapter_id,
                    messages=[entry],
                )
                db.add(row)
            else:
                # JSONB는 mutation 추적이 안 되므로 재할당으로 dirty 표시.
                row.messages = [*row.messages, entry]
            return entry

    async def all_threads(
        self, session_id: str
    ) -> dict[str, dict[str, list[dict[str, Any]]]]:
        """C4 2단계 중첩: ``{trackId: {chapterId: [Message, ...]}}``."""
        sid = _parse_id(session_id)
        if sid is None:
            return {}
        async with session_scope() as db:
            result = await db.execute(select(Thread).where(Thread.session_id == sid))
            out: dict[str, dict[str, list[dict[str, Any]]]] = {}
            for t in result.scalars().all():
                out.setdefault(t.track_id, {})[t.chapter_id] = list(t.messages)
            return out


store = SessionStore()
