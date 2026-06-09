"""SessionStore 단위 테스트 (SQLite + aiosqlite, 네트워크/Postgres 불필요).

스토어 단독 검증: create -> save_material -> write_curriculum ->
append_to_thread(여러 track/chapter) -> all_threads가 C4 2단계 모양으로 나오는지,
그리고 lifecycle/planning/curriculum 메서드들이 store 단독으로 동작하는지.

set_known 등 runtime 흐름과는 무관하게 store만 본다.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import pytest_asyncio


@pytest_asyncio.fixture
async def store(tmp_path: Path, monkeypatch):
    """tmp dir에 SQLite DB + 로컬 캐시 루트를 잡고, 모듈 전역을 리셋한 뒤 스키마를
    생성하고 DEFAULT_USER를 시드한 새 SessionStore를 준다.

    멀티유저 전환 후 SessionRow.user_id가 users FK를 가지므로, create 전에
    DEFAULT_USER 행이 있어야 한다. 바이너리는 storage(R2+캐시)를 타므로 R2 비활성
    상태에서 캐시 루트를 tmp로 돌린다(로컬 디스크만 쓰는 경로)."""
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")

    from app import db
    from app import storage as storage_mod

    # storage 인스턴스의 캐시 루트를 tmp로 (R2는 env 없어 비활성 → 로컬만).
    monkeypatch.setattr(storage_mod.storage, "_cache_root", tmp_path / "data")

    await db.dispose_engine()

    from app.models import Base, DEFAULT_USER_EMAIL, DEFAULT_USER_ID, User

    engine = db.get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # DEFAULT_USER 시드 (create의 user_id FK 충족).
    async with db.session_scope() as s:
        s.add(User(id=DEFAULT_USER_ID, email=DEFAULT_USER_EMAIL, name="Local"))

    from app import sessions as sessions_mod

    yield sessions_mod.SessionStore()

    await db.dispose_engine()


async def test_lifecycle_create_get_list_delete(store):
    s = await store.create(filename="paper.pdf", kind="pdf", title="테스트 논문")
    assert s.id
    assert s.kind == "pdf"
    assert s.status == "ready"

    got = await store.get(s.id)
    assert got is not None
    assert got.title == "테스트 논문"

    listed = await store.list()
    assert [x.id for x in listed] == [s.id]

    await store.delete(s.id)
    assert await store.get(s.id) is None
    assert await store.list() == []


async def test_get_missing_returns_none(store):
    assert await store.get("not-a-uuid") is None
    assert await store.get("00000000-0000-0000-0000-000000000099") is None


async def test_planning_state(store):
    s = await store.create(
        filename="p.pdf", kind="pdf", title="플래닝", status="planning"
    )
    await store.set_progress(s.id, 2, 5)
    got = await store.get(s.id)
    assert got.progress_done == 2 and got.progress_total == 5
    assert got.status == "planning"

    await store.mark_error(s.id, "boom")
    got = await store.get(s.id)
    assert got.status == "error"
    assert got.planning_error == "boom"


async def test_fail_stuck_planning(store):
    a = await store.create(filename="a.pdf", kind="pdf", title="A", status="planning")
    b = await store.create(filename="b.pdf", kind="pdf", title="B", status="ready")
    n = await store.fail_stuck_planning("재시작으로 중단됨")
    assert n == 1
    assert (await store.get(a.id)).status == "error"
    assert (await store.get(b.id)).status == "ready"


async def test_save_material_writes_local_disk(store):
    s = await store.create(filename="paper.pdf", kind="pdf", title="자료")
    await store.save_material(s, b"%PDF-1.7 fake")
    assert s.material_path.exists()
    assert s.material_path.read_bytes() == b"%PDF-1.7 fake"

    await store.save_pages(s, 7)
    assert s.page_count == 7
    assert (await store.get(s.id)).page_count == 7


async def test_curriculum_roundtrip(store):
    s = await store.create(filename="p.pdf", kind="pdf", title="원제")
    assert await store.read_curriculum(s.id) is None

    curri = {"paper": {"title": "T"}, "tracks": [{"id": "paper", "chapters": []}]}
    await store.mark_ready(s.id, curri, title="새 제목")
    got = await store.get(s.id)
    assert got.status == "ready"
    assert got.title == "새 제목"
    assert await store.read_curriculum(s.id) == curri

    curri2 = {**curri, "sources": [{"id": "s1"}]}
    await store.write_curriculum(s.id, curri2)
    assert await store.read_curriculum(s.id) == curri2


async def test_threads_two_level_nesting(store):
    """C4: all_threads는 {trackId: {chapterId: [Message]}} 2단계 중첩."""
    s = await store.create(filename="p.pdf", kind="pdf", title="threads")

    await store.append_to_thread(s.id, "prereq", "ch1", "user", "안녕")
    await store.append_to_thread(s.id, "prereq", "ch1", "assistant", "반가워요")
    await store.append_to_thread(
        s.id, "prereq", "ch2", "user", "kickoff", synthetic=True
    )
    await store.append_to_thread(s.id, "paper", "c1", "assistant", "논문 설명")

    # read_thread는 (track, chapter) 단위.
    ch1 = await store.read_thread(s.id, "prereq", "ch1")
    assert [m["content"] for m in ch1] == ["안녕", "반가워요"]
    assert all("ts" in m for m in ch1)
    assert all("synthetic" not in m for m in ch1)

    ch2 = await store.read_thread(s.id, "prereq", "ch2")
    assert ch2[0]["synthetic"] is True

    # 없는 트랙/챕터는 빈 리스트.
    assert await store.read_thread(s.id, "trends", "x") == []

    allt = await store.all_threads(s.id)
    assert set(allt.keys()) == {"prereq", "paper"}
    assert set(allt["prereq"].keys()) == {"ch1", "ch2"}
    assert set(allt["paper"].keys()) == {"c1"}
    assert [m["content"] for m in allt["prereq"]["ch1"]] == ["안녕", "반가워요"]
    assert allt["paper"]["c1"][0]["content"] == "논문 설명"
    # Message 모양: role/content/ts (+ synthetic?)
    msg = allt["prereq"]["ch1"][0]
    assert set(msg.keys()) == {"role", "content", "ts"}


async def test_threads_empty_for_new_session(store):
    s = await store.create(filename="p.pdf", kind="pdf", title="empty")
    assert await store.all_threads(s.id) == {}


async def test_delete_removes_local_dir(store):
    s = await store.create(filename="p.pdf", kind="pdf", title="del")
    await store.save_material(s, b"data")
    from app import storage as storage_mod

    prefix_dir = storage_mod.storage.cache_path(s._prefix)
    assert prefix_dir.exists()
    await store.delete(s.id)
    assert not prefix_dir.exists()
