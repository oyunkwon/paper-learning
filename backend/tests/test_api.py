"""API 통합 테스트 (httpx ASGITransport + SQLite, 네트워크/키 없음).

plan_paper의 LLM/retrieval은 monkeypatch로 가짜 4트랙 커리큘럼을 즉시 반환하게
스텁한다. 커버하는 흐름:
  health -> 업로드(작은 md) -> 백그라운드 플래닝 완료까지 폴링 -> get(C4 모양)
  -> known 토글(C3) -> activate -> pass.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.schema import normalize_curriculum


def _fake_curriculum() -> dict[str, Any]:
    raw = {
        "paper": {"title": "테스트 논문", "year": 2020, "arxivId": "2001.00001"},
        "tracks": [
            {
                "id": "paper",
                "chapters": [
                    {"id": "c1", "title": "주장", "concepts": ["핵심주장", "결과"],
                     "pageStart": 1, "pageEnd": 3},
                ],
            },
            {
                "id": "prereq",
                "groups": [
                    {"id": "g_la", "title": "선형대수", "textbook": "Strang",
                     "chapterIds": ["p1"]},
                ],
                "chapters": [
                    {"id": "p1", "title": "벡터공간", "concepts": ["기저", "차원"]},
                    {"id": "p2", "title": "고유값", "concepts": ["고유벡터"]},
                ],
            },
            {
                "id": "landscape",
                "chapters": [
                    {"id": "l1", "title": "RNN", "concepts": ["seq2seq"],
                     "sourceIds": ["s1"]},
                ],
            },
        ],
        "sources": [
            {"id": "s1", "title": "Seq2Seq", "type": "paper",
             "retrievedFrom": "openalex", "year": 2014},
        ],
    }
    return normalize_curriculum(raw)


@pytest_asyncio.fixture
async def client(tmp_path: Path, monkeypatch):
    """SQLite DB + tmp 데이터 디렉터리를 잡고, plan_paper를 가짜로 스텁한 뒤
    ASGITransport로 앱에 붙은 AsyncClient를 준다."""
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path}")
    monkeypatch.setenv("RELAY_API_KEY", "test-key")  # load_settings가 통과하도록
    # 인증 dev bypass: Google 미설정 + AUTH_DEV_BYPASS=1 → current_user가
    # DEFAULT_USER로 해소된다(실제 OAuth 라운드트립 없이 테스트).
    monkeypatch.setenv("AUTH_DEV_BYPASS", "1")
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)

    from app import db
    from app import storage as storage_mod

    # storage 캐시 루트를 tmp로 (R2 비활성 → 로컬 디스크만).
    monkeypatch.setattr(storage_mod.storage, "_cache_root", tmp_path / "data")

    # plan_paper를 즉시 가짜 커리큘럼 반환으로 스텁(네트워크/LLM 없음).
    import app.main as main_mod

    async def fake_plan_paper(*, on_progress=None, **kwargs):
        if on_progress:
            await on_progress(6, 6)
        return _fake_curriculum()

    monkeypatch.setattr(main_mod, "plan_paper", fake_plan_paper)

    await db.dispose_engine()
    from app.models import Base, DEFAULT_USER_EMAIL, DEFAULT_USER_ID, User

    engine = db.get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # DEFAULT_USER 시드 (dev bypass가 이 유저로 해소되고, FK를 충족).
    async with db.session_scope() as s:
        s.add(User(id=DEFAULT_USER_ID, email=DEFAULT_USER_EMAIL, name="Local"))

    transport = ASGITransport(app=main_mod.app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    await db.dispose_engine()


async def _upload_and_wait(client: AsyncClient) -> str:
    """작은 md를 업로드하고 백그라운드 플래닝이 끝날 때까지 폴링. session id 반환."""
    resp = await client.post(
        "/api/sessions",
        files={"file": ("note.md", "# 제목\n\n본문 내용".encode("utf-8"), "text/markdown")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "planning"
    assert body["curriculum"] is None
    sid = body["id"]

    # 백그라운드 task가 같은 이벤트 루프에서 돈다. ready까지 폴링.
    for _ in range(50):
        await asyncio.sleep(0.02)
        got = await client.get(f"/api/sessions/{sid}")
        if got.json()["status"] == "ready":
            return sid
    raise AssertionError("planning did not finish")


# --- 테스트 -----------------------------------------------------------------


async def test_health(client: AsyncClient):
    resp = await client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["key_present"] is True   # RELAY_API_KEY 세팅됨


async def test_upload_plan_get_c4_shape(client: AsyncClient):
    sid = await _upload_and_wait(client)

    got = await client.get(f"/api/sessions/{sid}")
    assert got.status_code == 200
    data = got.json()

    # C4: summary 필드 + curriculum + threads.
    assert data["id"] == sid
    assert data["status"] == "ready"
    assert data["kind"] == "md"
    assert "progress" in data and set(data["progress"]) == {"done", "total"}

    curr = data["curriculum"]
    assert curr is not None
    # 트랙 순서: paper, prereq, landscape (TRACK_ORDER).
    assert [t["id"] for t in curr["tracks"]] == ["paper", "prereq", "landscape"]
    assert curr["paper"]["title"] == "테스트 논문"

    # threads는 2단계 중첩(아직 비어 있음).
    assert data["threads"] == {}


async def test_list_and_curriculum_endpoints(client: AsyncClient):
    sid = await _upload_and_wait(client)

    listed = await client.get("/api/sessions")
    assert [s["id"] for s in listed.json()["sessions"]] == [sid]

    curr = await client.get(f"/api/sessions/{sid}/curriculum")
    assert curr.status_code == 200
    assert curr.json()["tracks"]


async def test_known_toggle_c3(client: AsyncClient):
    sid = await _upload_and_wait(client)

    # prereq p1을 known=true로 토글 -> done 처리 + 갱신된 커리큘럼 반환.
    resp = await client.post(
        f"/api/sessions/{sid}/known",
        json={"trackId": "prereq", "chapterId": "p1", "known": True},
    )
    assert resp.status_code == 200
    curr = resp.json()
    prereq = next(t for t in curr["tracks"] if t["id"] == "prereq")
    p1 = next(c for c in prereq["chapters"] if c["id"] == "p1")
    assert p1["known"] is True
    assert p1["status"] == "done"

    # 영속화 확인: 다시 읽어도 known 유지.
    got = await client.get(f"/api/sessions/{sid}")
    prereq2 = next(t for t in got.json()["curriculum"]["tracks"] if t["id"] == "prereq")
    assert next(c for c in prereq2["chapters"] if c["id"] == "p1")["known"] is True


async def test_known_rejects_non_bool(client: AsyncClient):
    sid = await _upload_and_wait(client)
    resp = await client.post(
        f"/api/sessions/{sid}/known",
        json={"trackId": "prereq", "chapterId": "p1", "known": "yes"},
    )
    assert resp.status_code == 400


async def test_activate_chapter(client: AsyncClient):
    sid = await _upload_and_wait(client)

    # prereq p2는 처음에 locked. activate -> active.
    resp = await client.post(
        f"/api/sessions/{sid}/activate",
        json={"trackId": "prereq", "chapterId": "p2"},
    )
    assert resp.status_code == 200
    prereq = next(t for t in resp.json()["tracks"] if t["id"] == "prereq")
    p2 = next(c for c in prereq["chapters"] if c["id"] == "p2")
    assert p2["status"] == "active"


async def test_pass_concept(client: AsyncClient):
    sid = await _upload_and_wait(client)

    # paper c1의 현재 개념(0)을 건너뛰기.
    resp = await client.post(
        f"/api/sessions/{sid}/pass",
        json={"trackId": "paper", "chapterId": "c1"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["passedIndex"] == 0
    assert body["chapterDone"] is False
    paper = next(t for t in body["curriculum"]["tracks"] if t["id"] == "paper")
    c1 = next(c for c in paper["chapters"] if c["id"] == "c1")
    assert c1["conceptsDone"][0] is True


async def test_unknown_track_and_chapter_404(client: AsyncClient):
    sid = await _upload_and_wait(client)
    r1 = await client.post(
        f"/api/sessions/{sid}/known",
        json={"trackId": "nope", "chapterId": "p1", "known": True},
    )
    assert r1.status_code == 404
    r2 = await client.post(
        f"/api/sessions/{sid}/activate",
        json={"trackId": "prereq", "chapterId": "ghost"},
    )
    assert r2.status_code == 404


async def test_missing_session_404(client: AsyncClient):
    resp = await client.get("/api/sessions/00000000-0000-0000-0000-000000000099")
    assert resp.status_code == 404


async def test_unsupported_extension_rejected(client: AsyncClient):
    resp = await client.post(
        "/api/sessions",
        files={"file": ("bad.txt", b"hello", "text/plain")},
    )
    assert resp.status_code == 400


async def test_rename_and_delete(client: AsyncClient):
    sid = await _upload_and_wait(client)

    renamed = await client.patch(
        f"/api/sessions/{sid}", json={"title": "새 제목"}
    )
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "새 제목"

    deleted = await client.delete(f"/api/sessions/{sid}")
    assert deleted.status_code == 200
    assert (await client.get(f"/api/sessions/{sid}")).status_code == 404
