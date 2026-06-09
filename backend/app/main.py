"""FastAPI 앱: W1 SessionStore + W2 튜터 루프 위의 얇은 라우트.

learning `main.py` 골격을 차용하되 **인증/R2/세션쿠키를 제거**하고 멀티트랙으로
바꿨다(로컬 단일 유저). 플래닝은 업로드 후 백그라운드 task로 돌고(큰 PDF의 요청
타임아웃 회피), 클라이언트는 GET /api/sessions/{id}로 status+progress를 폴링한다.
채팅 턴은 POST에서 NDJSON 프레임(W2 stream_turn 출력)을 그대로 흘린다.
127.0.0.1 바인드, 인증 없음.

요청/응답은 docs/WORKSTREAMS.md의 C2/C3/C4 + activate/pass 계약과 정확히 일치한다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app import auth, material, runtime, tutor
from app.auth import current_user
from app.config import (
    ConfigError,
    load_auth_settings,
    load_retrieval_settings,
    load_settings,
)
from app.db import dispose_engine
from app.llm import LLMError, build_client
from app.material import MaterialError
from app.models import User
from app.planner.plan import plan_paper
from app.retrieval.http import CachedHTTP
from app.schema import CurriculumError
from app.sessions import Session, store
from app.tutor_prompts import tutor_kickoff_user, tutor_pass_user

# 우리 로거를 터미널에 노출. uvicorn 루트 설정과 무관하게 진행 로그가 보이도록
# "paper" 부모 로거에 스트림 핸들러를 단다.
_log_parent = logging.getLogger("paper")
_log_parent.setLevel(logging.INFO)
if not _log_parent.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", "%H:%M:%S")
    )
    _log_parent.addHandler(_h)
    _log_parent.propagate = False
log = logging.getLogger("paper.main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # dev bypass / 로컬 단일 유저를 위해 시드 유저를 보장한다(인증 꺼졌을 때
    # current_user가 DEFAULT_USER로 해소되므로 그 행이 있어야 한다).
    try:
        await _seed_default_user()
    except Exception:  # noqa: BLE001
        log.exception("[startup] failed to seed default user")
    # 백그라운드 플래닝 task는 워커 메모리에만 사니, 재배포/재시작 시 'planning'에
    # 갇힌 세션이 생긴다. 시작 시 error로 풀어 사용자가 재업로드하게 한다.
    try:
        n = await store.fail_stuck_planning(
            "서버 재시작으로 커리큘럼 생성이 중단되었습니다. 다시 업로드해 주세요."
        )
        if n:
            log.info("[startup] cleaned up %d stuck planning session(s)", n)
    except Exception:  # noqa: BLE001 - 정리 실패가 시작을 막지 않게
        log.exception("[startup] failed to clean up stuck planning sessions")
    yield
    await dispose_engine()


async def _seed_default_user() -> None:
    """DEFAULT_USER 행을 멱등하게 보장. dev bypass에서 current_user가 이 행을
    반환하고, 로컬 데이터가 이 유저 소유로 만들어진다."""
    from app.db import session_scope
    from app.models import DEFAULT_USER_EMAIL, DEFAULT_USER_ID, User as UserModel

    async with session_scope() as db:
        existing = await db.get(UserModel, DEFAULT_USER_ID)
        if existing is None:
            db.add(
                UserModel(
                    id=DEFAULT_USER_ID,
                    email=DEFAULT_USER_EMAIL,
                    name="Local User",
                )
            )


app = FastAPI(title="paper-learning", lifespan=lifespan)

# 서명된 세션 쿠키(인증된 유저의 uuid를 담는다). 시크릿/플래그는 auth 설정에서.
_auth_cfg = load_auth_settings()
app.add_middleware(
    SessionMiddleware,
    secret_key=_auth_cfg.session_secret,
    session_cookie="paper_session",
    https_only=_auth_cfg.cookie_secure,
    same_site="lax",
)
app.include_router(auth.router)

ALLOWED_EXT = {".pdf": "pdf", ".md": "md"}
SOURCE_CONTENT_TYPE = {
    "pdf": "application/pdf",
    "md": "text/markdown; charset=utf-8",
}


def _err(status: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"message": message}})


@app.get("/api/health")
async def health() -> dict[str, Any]:
    auth_cfg = load_auth_settings()
    return {
        "ok": True,
        "key_present": load_settings(require_key=False).has_key,
        "auth": {
            "google": auth_cfg.google_configured,
            "devBypass": auth_cfg.dev_bypass,
        },
    }


# --- 세션 생성 + 백그라운드 플래닝 ------------------------------------------


@app.post("/api/sessions")
async def create_session(
    file: UploadFile = File(...), user: User = Depends(current_user)
) -> Any:
    filename = file.filename or "paper"
    ext = Path(filename).suffix.lower()
    if ext not in ALLOWED_EXT:
        return _err(400, f"지원하지 않는 파일 형식입니다: {ext or '(없음)'}")
    kind = ALLOWED_EXT[ext]

    data = await file.read()
    if not data:
        return _err(400, "빈 파일입니다.")

    try:
        load_settings()  # relay 키 없으면 빠르게 실패
    except ConfigError as e:
        return _err(503, str(e))

    # 'planning' 상태로 세션을 만들고 업로드 바이트를 저장한다. 느린 작업(페이지
    # 렌더링, retrieval, LLM 플래닝)은 전부 백그라운드로 돌려 이 요청은 1초 안에
    # 응답한다. 클라이언트는 GET /api/sessions/{id}로 status+progress를 폴링.
    session = await store.create(
        filename=filename, kind=kind, title=filename, user_id=user.id,
        status="planning",
    )
    await store.save_material(session, data)
    log.info(
        "[upload %s] %s (%s, %d KB) created",
        session.id[:8], filename, kind, len(data) // 1024,
    )

    # 렌더링 + 플래닝을 백그라운드로 시작. GC로 취소되지 않게 strong ref 유지.
    task = asyncio.create_task(_run_planning(session, filename))
    _PLANNING_TASKS.add(task)
    task.add_done_callback(_PLANNING_TASKS.discard)

    # 지금 응답 — curriculum은 null, status는 'planning'.
    return {**session.to_summary(), "curriculum": None}


# 진행 중 플래닝 task의 strong ref (create_task는 GC 대상이라 실행 중 취소될 수 있음).
_PLANNING_TASKS: set[asyncio.Task[Any]] = set()


async def _run_planning(session: Session, filename: str) -> None:
    """백그라운드 파이프라인: (pdf면) 페이지 렌더 -> plan_paper -> mark_ready.
    예외 시 mark_error. learning의 background task 패턴 그대로."""
    try:
        settings = load_settings()
    except ConfigError as e:
        await store.mark_error(session.id, str(e))
        return

    # 클라이언트가 즉시 바를 보이도록 미정 total을 seed. 플래너가 단계 수를 알면
    # 정밀화한다 (plan_paper가 on_progress로 _TOTAL_STEPS를 보고함).
    await store.set_progress(session.id, 0, 1)

    pages_dir: Path | None = None
    if session.kind == "pdf":
        # 렌더링은 동기 CPU 루프이므로 이벤트 루프 밖(스레드)에서 돌려 다른 요청을
        # 막지 않게 한다.
        try:
            t0 = time.monotonic()
            n = await asyncio.to_thread(
                material.render_pdf_pages, session.material_path, session.pages_dir
            )
            await store.save_pages(session, n)
            pages_dir = session.pages_dir
            log.info(
                "[upload %s] rendered %d pages in %.1fs",
                session.id[:8], n, time.monotonic() - t0,
            )
        except MaterialError as e:
            await store.mark_error(session.id, str(e))
            return
        except Exception as e:  # noqa: BLE001 - 세션이 planning에 갇히지 않게
            log.exception("[upload %s] rendering crashed", session.id[:8])
            await store.mark_error(session.id, f"페이지 렌더링 중 오류: {e}")
            return

    async def on_progress(done: int, total: int) -> None:
        await store.set_progress(session.id, done, total)

    rcfg = load_retrieval_settings()
    client = build_client(settings)
    http = CachedHTTP(
        cache_dir=rcfg.cache_dir,
        contact_email=rcfg.contact_email,
        timeout_s=rcfg.timeout_s,
        max_retries=rcfg.max_retries,
    )
    t_plan = time.monotonic()
    try:
        curriculum = await plan_paper(
            kind=session.kind,
            material_path=session.material_path,
            pages_dir=pages_dir,
            client=client,
            model=settings.planner_model,
            http=http,
            on_progress=on_progress,
        )
    except (CurriculumError, LLMError) as e:
        log.warning("[upload %s] planning failed: %s", session.id[:8], e)
        await store.mark_error(session.id, f"커리큘럼 생성 실패: {e}")
        return
    except Exception as e:  # noqa: BLE001 - 세션이 planning에 갇히지 않게
        log.exception("[upload %s] planning crashed", session.id[:8])
        await store.mark_error(session.id, f"커리큘럼 생성 중 오류: {e}")
        return
    finally:
        await client.aclose()
        await http.aclose()

    n_tracks = len(curriculum.get("tracks", []))
    paper = curriculum.get("paper") if isinstance(curriculum.get("paper"), dict) else {}
    title = (paper or {}).get("title") or filename
    log.info(
        "[upload %s] curriculum ready (%d tracks) in %.1fs total",
        session.id[:8], n_tracks, time.monotonic() - t_plan,
    )
    await store.mark_ready(session.id, curriculum, title=title)


# --- 조회 / 메타 -------------------------------------------------------------


@app.get("/api/sessions")
async def list_sessions(user: User = Depends(current_user)) -> dict[str, Any]:
    sessions = await store.list(user_id=user.id)
    return {"sessions": [s.to_summary() for s in sessions]}


@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str, user: User = Depends(current_user)) -> Any:
    """C4: summary + curriculum + threads(2단계 중첩)."""
    session = await _require(session_id, user)
    return {
        **session.to_summary(),
        "curriculum": await store.read_curriculum(session.id),
        "threads": await store.all_threads(session.id),
    }


@app.patch("/api/sessions/{session_id}")
async def rename_session(
    session_id: str, request: Request, user: User = Depends(current_user)
) -> Any:
    await _require(session_id, user)
    body = await request.json()
    title = (body or {}).get("title")
    if not isinstance(title, str) or not title.strip():
        return _err(400, "제목이 비어 있습니다.")
    session = await store.set_title(session_id, title.strip()[:200])
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    return session.to_summary()


@app.delete("/api/sessions/{session_id}")
async def delete_session(
    session_id: str, user: User = Depends(current_user)
) -> dict[str, Any]:
    await _require(session_id, user)
    await store.delete(session_id)
    return {"ok": True}


@app.get("/api/sessions/{session_id}/material")
async def get_material(session_id: str, user: User = Depends(current_user)) -> Any:
    session = await _require(session_id, user)
    # R2에서 캐시로 rehydrate(재배포로 캐시가 비워졌을 수 있음).
    await store.storage_ensure_material(session)
    if not session.material_path.exists():
        raise HTTPException(status_code=404, detail="material not found")
    return FileResponse(
        session.material_path,
        media_type=SOURCE_CONTENT_TYPE[session.kind],
        filename=session.filename,
        content_disposition_type="inline",
    )


@app.get("/api/sessions/{session_id}/curriculum")
async def get_curriculum(session_id: str, user: User = Depends(current_user)) -> Any:
    await _require(session_id, user)
    c = await store.read_curriculum(session_id)
    if c is None:
        raise HTTPException(status_code=404, detail="curriculum not found")
    return c


# --- 채팅 (C2) ---------------------------------------------------------------


@app.post("/api/sessions/{session_id}/chat")
async def chat(
    session_id: str, request: Request, user: User = Depends(current_user)
) -> Any:
    """C2: body {trackId, chapterId, message?, kickoff?, pass?} -> NDJSON 스트림.

    (trackId, chapterId)로 트랙/챕터를 찾고(runtime.find_chapter), 적절한 user 턴을
    스레드에 쌓은 뒤 tutor.stream_turn 프레임을 NDJSON으로 흘린다."""
    session = await _require(session_id, user)
    curriculum = await store.read_curriculum(session.id)
    if curriculum is None:
        return _err(409, "커리큘럼이 아직 준비되지 않았습니다.")

    body = await request.json() or {}
    track_id = body.get("trackId")
    chapter_id = body.get("chapterId")
    user_text = body.get("message")
    is_kickoff = bool(body.get("kickoff"))
    is_pass = bool(body.get("pass"))

    if not isinstance(track_id, str) or not track_id:
        return _err(400, "trackId가 필요합니다.")
    track = runtime.find_track(curriculum, track_id)
    if track is None:
        return _err(404, "트랙을 찾을 수 없습니다.")

    # 챕터 해석: chapterId가 오면 그걸로, 아니면 트랙의 active 챕터로.
    if isinstance(chapter_id, str) and chapter_id:
        chapter = runtime.find_chapter(curriculum, track_id, chapter_id)
    else:
        chapter = runtime.active_chapter(curriculum, track_id)
    if chapter is None:
        return _err(404, "챕터를 찾을 수 없습니다.")
    chapter_id = chapter["id"]

    if is_pass:
        # 학습자가 현재 개념을 건너뛰었다(이미 /pass로 done 처리됨). 합성 턴을 넣어
        # 튜터가 그 개념을 다시 묻지 않고 다음 개념으로 넘어가게 한다.
        next_index = runtime.current_concept_index(chapter)
        passed = tutor_pass_user(chapter_id, next_index)
        await store.append_to_thread(
            session.id, track_id, chapter_id, "user", passed, synthetic=True
        )
    elif not is_kickoff:
        if not isinstance(user_text, str) or not user_text.strip():
            return _err(400, "메시지가 비어 있습니다.")
        await store.append_to_thread(
            session.id, track_id, chapter_id, "user", user_text.strip()
        )
    elif await store.read_thread(session.id, track_id, chapter_id):
        # kickoff 요청이지만 이 챕터 스레드가 이미 시작됨 — 무시.
        return _err(409, "이미 시작된 챕터입니다.")
    else:
        kickoff = tutor_kickoff_user(chapter_id, chapter.get("title", ""))
        await store.append_to_thread(
            session.id, track_id, chapter_id, "user", kickoff, synthetic=True
        )

    # 튜터가 볼 챕터 스레드(위 user/kickoff append 이후).
    thread = await store.read_thread(session.id, track_id, chapter_id)
    await store.ensure_cached(session)

    try:
        settings = load_settings()
    except ConfigError as e:
        return _err(503, str(e))

    client = build_client(settings)

    async def gen() -> Any:
        collected: dict[str, Any] = {"content": ""}

        async def _save_curriculum(updated: dict[str, Any]) -> None:
            await store.write_curriculum(session.id, updated)

        try:
            async for frame in tutor.stream_turn(
                session,
                curriculum,
                track,
                chapter,
                thread,
                client,
                model=settings.tutor_model,
                on_curriculum_change=_save_curriculum,
            ):
                if frame.get("type") == "done":
                    collected["content"] = frame.get("content", "")
                yield json.dumps(frame, ensure_ascii=False) + "\n"
        except LLMError as e:
            yield json.dumps(
                {"type": "error", "message": str(e)}, ensure_ascii=False
            ) + "\n"
        finally:
            # 어시스턴트의 가시 응답을 영속화(sentinel은 이미 제거됨).
            if collected["content"]:
                await store.append_to_thread(
                    session.id, track_id, chapter_id, "assistant", collected["content"]
                )
            await client.aclose()

    return StreamingResponse(gen(), media_type="application/x-ndjson")


# --- activate / pass / known -------------------------------------------------


@app.post("/api/sessions/{session_id}/activate")
async def activate_chapter(
    session_id: str, request: Request, user: User = Depends(current_user)
) -> Any:
    """activate 계약: body {trackId, chapterId} -> 챕터 활성화 -> 갱신된 curriculum."""
    session = await _require(session_id, user)
    curriculum = await store.read_curriculum(session.id)
    if curriculum is None:
        return _err(409, "커리큘럼이 아직 준비되지 않았습니다.")
    body = await request.json() or {}
    track_id = body.get("trackId")
    chapter_id = body.get("chapterId")
    if not isinstance(track_id, str) or not track_id:
        return _err(400, "trackId가 필요합니다.")
    if not isinstance(chapter_id, str) or not chapter_id:
        return _err(400, "chapterId가 필요합니다.")
    chapter = runtime.find_chapter(curriculum, track_id, chapter_id)
    if chapter is None:
        return _err(404, "챕터를 찾을 수 없습니다.")
    runtime.activate_chapter(chapter)
    await store.write_curriculum(session.id, curriculum)
    return curriculum


@app.post("/api/sessions/{session_id}/pass")
async def pass_concept(
    session_id: str, request: Request, user: User = Depends(current_user)
) -> Any:
    """pass 계약: body {trackId, chapterId} -> 현재 개념 건너뛰기(서버 마킹) ->
    {curriculum, passedIndex, chapterDone}. 클라이언트는 그 뒤 chat(pass=true)으로
    튜터가 다음 개념으로 넘어가게 한다."""
    session = await _require(session_id, user)
    curriculum = await store.read_curriculum(session.id)
    if curriculum is None:
        return _err(409, "커리큘럼이 아직 준비되지 않았습니다.")
    body = await request.json() or {}
    track_id = body.get("trackId")
    chapter_id = body.get("chapterId")
    if not isinstance(track_id, str) or not track_id:
        return _err(400, "trackId가 필요합니다.")
    if not isinstance(chapter_id, str) or not chapter_id:
        return _err(400, "chapterId가 필요합니다.")
    chapter = runtime.find_chapter(curriculum, track_id, chapter_id)
    if chapter is None:
        return _err(404, "챕터를 찾을 수 없습니다.")
    passed_index, chapter_done = runtime.pass_current_concept(chapter)
    if passed_index is None:
        return _err(409, "건너뛸 개념이 없습니다.")
    await store.write_curriculum(session.id, curriculum)
    return {
        "curriculum": curriculum,
        "passedIndex": passed_index,
        "chapterDone": chapter_done,
    }


@app.post("/api/sessions/{session_id}/known")
async def set_known(
    session_id: str, request: Request, user: User = Depends(current_user)
) -> Any:
    """C3: body {trackId, chapterId, known} -> prereq 챕터 known 토글 ->
    갱신된 curriculum 전체."""
    session = await _require(session_id, user)
    curriculum = await store.read_curriculum(session.id)
    if curriculum is None:
        return _err(409, "커리큘럼이 아직 준비되지 않았습니다.")
    body = await request.json() or {}
    track_id = body.get("trackId")
    chapter_id = body.get("chapterId")
    known = body.get("known")
    if not isinstance(track_id, str) or not track_id:
        return _err(400, "trackId가 필요합니다.")
    if not isinstance(chapter_id, str) or not chapter_id:
        return _err(400, "chapterId가 필요합니다.")
    if not isinstance(known, bool):
        return _err(400, "known은 boolean이어야 합니다.")
    chapter = runtime.find_chapter(curriculum, track_id, chapter_id)
    if chapter is None:
        return _err(404, "챕터를 찾을 수 없습니다.")
    runtime.set_known(chapter, known)
    await store.write_curriculum(session.id, curriculum)
    return curriculum


# --- 공통 ------------------------------------------------------------------


async def _require(session_id: str, user: User) -> Session:
    """세션을 가져오고 소유권을 강제한다. 비소유자는 404(403 아님) — 세션 존재가
    유저 간 새지 않게."""
    session = await store.get(session_id, owner_id=user.id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    return session


# --- 정적 SPA 서빙 (API 라우트 뒤에 마운트해 /api/*가 우선) ------------------
_DIST = Path(
    os.environ.get(
        "FRONTEND_DIST",
        str(Path(__file__).resolve().parents[2] / "frontend" / "dist"),
    )
)
if _DIST.exists():
    app.mount("/", StaticFiles(directory=str(_DIST), html=True), name="static")
