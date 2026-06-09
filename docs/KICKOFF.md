# 세션 킥오프 프롬프트

각 워크스트림 세션에 그대로 붙여넣는다. 모든 세션은 먼저
`docs/WORKSTREAMS.md`(인터페이스 계약 SSOT)와 `docs/DESIGN.md`(설계 배경)를
읽고 시작한다. 프로젝트 루트는 `/Users/oyunkwon/main/projects/paper-learning`.

공통 규칙:
- `docs/WORKSTREAMS.md`의 계약을 절대 임의 변경하지 않는다. 변경이 필요하면
  멈추고 보고한다 (다른 세션과 공유되는 계약이므로).
- 이미 완료된 자산(config/llm/material/jsonparse/schema/runtime/retrieval/
  planner)은 변경하지 말고 사용만 한다.
- 백엔드는 `cd backend && uv run ...`, Python 3.13 venv는 이미 있음.
- 한국어 주석/문구, learning 프로젝트(`../learning`)의 코드 스타일을 따른다.
- 작업 끝에 테스트로 검증하고 결과를 보고한다.

---

## W1 — DB / 세션 레이어

너는 paper-learning의 DB/세션 레이어를 구현한다. 먼저
`docs/WORKSTREAMS.md`와 `docs/DESIGN.md`를 읽어라. 참고 구현은
`../learning/backend/app/`의 `db.py`, `models.py`, `sessions.py`,
`storage.py`, `alembic/`다 (단 learning은 Postgres+R2+인증, 우리는 **로컬 디스크
+ 인증 없음**으로 단순화).

구현 대상 (`backend/app/`):
1. `db.py` — learning에서 거의 그대로 차용 (async SQLAlchemy 엔진 + session_scope).
2. `models.py`:
   - `SessionRow`: id(uuid), title, filename, kind('pdf'|'md'), page_count,
     curriculum(JSONB), status, planning_error, progress_done, progress_total,
     created_at, updated_at. **user_id 컬럼 없음** (로컬 단일 유저).
   - `Thread`: PK = (session_id, track_id, chapter_id), messages(JSONB).
     learning의 (session, chapter)에 **track_id를 추가**한 것.
3. `sessions.py`:
   - `Session` 데이터클래스: id/title/filename/kind/page_count/created_at/status/
     planning_error/progress_done/progress_total + `material_path`, `pages_dir`
     계산 프로퍼티(`config.DEFAULT_DATA_DIR` 아래 로컬 경로), `to_summary()`.
   - `SessionStore` (async, 전부 `session_scope` 사용):
     - lifecycle: `create`, `get`, `list`, `delete`
     - planning: `set_progress`, `mark_ready`, `mark_error`, `fail_stuck_planning`
     - material: `save_material`(로컬 디스크 쓰기), `save_pages`, `ensure_cached`
       (로컬만, R2 없음)
     - curriculum: `read_curriculum`, `write_curriculum`
     - threads: `read_thread(id, track, chapter)`,
       `append_to_thread(id, track, chapter, role, content, *, synthetic=False)`,
       `all_threads(id)` → **C4의 2단계 중첩** `{trackId: {chapterId: [msg]}}` 반환.
   - 모듈 끝에 `store = SessionStore()`.
4. `alembic/` + `alembic.ini`: 초기 마이그레이션 (sessions, threads 테이블).

경계 (반드시 지킬 것):
- SessionStore 공개 메서드 시그니처가 W3와의 계약 — 위 목록대로.
- `all_threads`/threads 반환은 **반드시 C4 2단계 모양**.
- Message dict 모양: `{"role","content","ts", "synthetic"?}` (C4).
- 스토리지는 로컬 디스크만. R2/boto3 도입 금지.

검증:
- dev Postgres가 없으면 SQLite+aiosqlite로 도는 단위 테스트를 작성(테스트는
  `tests/test_sessions.py`). create→save_material→write_curriculum→
  append_to_thread(여러 track/chapter)→all_threads가 C4 모양으로 나오는지,
  set_known 흐름과 무관하게 store 단독으로 검증.
- `uv run pytest tests/test_sessions.py -q` 통과.
- 기존 테스트(`uv run pytest -m "not live" -q`)를 깨지 않는다.

보고: 구현한 store 메서드 시그니처 목록 + threads 반환 예시 JSON.

---

## W2 — 튜터 루프 (트랙 인식)

너는 paper-learning의 소크라테스 튜터 루프를 구현한다. 먼저
`docs/WORKSTREAMS.md`와 `docs/DESIGN.md`를 읽어라. 참고/기반 구현은
`../learning/backend/app/tutor.py`와 `prompts.py`(TUTOR_SYSTEM)다 — 이 코드의
sentinel 파싱·가드 로직은 검증된 보석이니 **그대로 가져오고**, 멀티트랙으로만
확장한다.

이미 있는 자산: `app/runtime.py`(트랙 인식 chapter mutation — 사용만),
`app/schema.py`(자료구조), `app/llm.py`(LLMClient), `app/material.py`.

구현 대상 (`backend/app/`):
1. `tutor.py`:
   - learning에서 **그대로 포팅**: `_drain_sentinels`, `_LoopGuard`,
     `_SpeakerLabelGuard`, `_split_safe`, 모든 sentinel 정규식
     (`<<TEACHING:cid:idx>>`/`<<CONCEPT_DONE:cid:idx>>`/`<<MASTERED:cid>>`).
   - mutation은 `app.runtime`의 chapter-scoped 함수 사용(chapter dict 직접 전달):
     `mark_concept_done`, `mark_concepts_before`, `mark_chapter_done`.
   - `stream_turn(session, curriculum, track, chapter, thread, client, *, model,
     on_curriculum_change)` — track을 인자로 받는다. yield 프레임은 **C2 스키마
     정확히 일치** (token/concept_done/mastered/done).
   - `build_messages(session, curriculum, track, chapter, thread)`: 트랙 kind별 분기
     - `prereq`(dependency): 소크라테스. 자료 페이지 없이 교재 기반 개념 설명+질문.
     - `paper`(reading): 논문 원문 페이지(chapter.pageStart/pageEnd) 주입
       (material.pdf_page_text 우선, 없으면 이미지) + 원문 인용 강조. learning과
       가장 가까움.
     - `landscape`/`trends`(reading): **C5** — chapter.sourceIds를
       curriculum.sources에서 lookup해 "참고 소스(제목/저자/연도/요약)" 텍스트
       블록 주입. 논문 페이지는 안 씀.
2. `tutor_prompts.py`: TUTOR_SYSTEM의 트랙별 변형 4종 + kickoff/pass user 헬퍼
   (learning의 `tutor_kickoff_user`/`tutor_pass_user` 차용). 진행신호 규칙은 모든
   트랙 공통(learning 그대로). 수식 LaTeX/KaTeX 규칙도 유지.

경계 (반드시 지킬 것):
- `stream_turn` 출력 프레임 = C2 스키마. sentinel의 chapterId는 파싱된 그대로.
- 입력은 dict들(curriculum/track/chapter/thread) — **SessionStore에 의존하지
  않는다**. on_curriculum_change는 async 콜백.
- sentinel 문법/진행신호 의미는 learning과 동일하게 유지(트랙 추가만).

검증:
- `tests/test_tutor.py`: FakeLLMClient(스트림을 흉내내는 가짜)로 stream_turn을
  구동, (a) sentinel이 제거된 깨끗한 token, (b) concept_done/mastered 프레임,
  (c) 자문자답/루프 컷이 작동하는지 단위 테스트. 트랙별 build_messages가 알맞은
  컨텍스트(소스 블록 등)를 넣는지도 확인.
- `uv run pytest tests/test_tutor.py -q` 통과, 기존 테스트 불파괴.

보고: stream_turn 시그니처 + 트랙별 build_messages가 주입하는 컨텍스트 요약.

---

## W3 — FastAPI 라우트 + 백그라운드 플래닝  (W1·W2 완료 후 착수)

너는 paper-learning의 HTTP API를 구현한다. 먼저 `docs/WORKSTREAMS.md`와
`docs/DESIGN.md`를 읽어라. 참고 구현은 `../learning/backend/app/main.py`다
(인증/R2 제거, 멀티트랙·plan_paper로 교체).

전제: W1(`app/sessions.py`의 `store`)과 W2(`app/tutor.py`의 `stream_turn`)가
완료되어 있다. 시그니처는 `docs/WORKSTREAMS.md` 및 해당 파일을 직접 확인하라.

구현 대상 (`backend/app/main.py`):
- 라우트:
  - `GET /api/health`
  - `POST /api/sessions` — 업로드(pdf/md) → `status="planning"` 세션 생성 →
    백그라운드 `_run_planning` 시작 → 즉시 응답.
  - `GET /api/sessions` — 목록.
  - `GET /api/sessions/{id}` — **C4** (summary + curriculum + threads 2단계).
  - `PATCH /api/sessions/{id}` — 제목 변경.
  - `DELETE /api/sessions/{id}`.
  - `GET /api/sessions/{id}/material` — 원본 파일 서빙.
  - `GET /api/sessions/{id}/curriculum`.
  - `POST /api/sessions/{id}/chat` — **C2**. body trackId/chapterId 해석
    (`runtime.find_chapter`) → `tutor.stream_turn` → NDJSON.
  - `POST /api/sessions/{id}/activate` — **activate 계약**.
  - `POST /api/sessions/{id}/pass` — **pass 계약**.
  - `POST /api/sessions/{id}/known` — **C3**.
- `_run_planning(session, filename)`: (pdf면) `material.render_pdf_pages` →
  `store.save_pages` → `CachedHTTP` 생성(`config.load_retrieval_settings`) →
  `planner.plan_paper(...)` (on_progress→store.set_progress) →
  `store.mark_ready`. 예외 시 `store.mark_error`. learning의 background task
  패턴(strong ref set, lifespan에서 fail_stuck_planning) 그대로.
- 정적 SPA 서빙은 learning과 동일(`FRONTEND_DIST`).

경계:
- 요청/응답은 C2/C3/C4 + activate/pass 계약과 정확히 일치.
- chat 스트림 프레임은 W2 stream_turn이 내는 것을 그대로 NDJSON 직렬화.

검증:
- `tests/test_api.py`: httpx ASGITransport로 health, 업로드(작은 md로
  플래닝까지)→get(C4 모양)→known 토글→activate 흐름을 통합 테스트. plan_paper의
  LLM/retrieval은 monkeypatch로 가짜 커리큘럼 반환하도록 스텁(네트워크/키 없이
  도는 테스트).
- `uv run pytest tests/test_api.py -q` 통과, 기존 테스트 불파괴.

보고: 구현한 라우트 목록 + 통합 테스트가 커버하는 흐름.

---

## W4 — 프론트엔드 (멀티트랙 UI)

너는 paper-learning의 프론트엔드를 구현한다. 먼저 `docs/WORKSTREAMS.md`(특히
C2/C3/C4 + activate/pass 계약)와 `docs/DESIGN.md`를 읽어라. 참고 구현은
`../learning/frontend/`다 (React+Vite+TS+Tailwind v4+shadcn). 컴포넌트·스타일을
차용하고 멀티트랙으로 확장한다.

전제: 백엔드가 없어도 **mock 데이터**로 병렬 개발한다. API 계약(C2/C3/C4 +
activate/pass)만 지키면 나중에 실제 백엔드로 무수정 전환된다.

구현 대상 (`frontend/`):
- learning에서 차용: SessionSidebar, UploadZone, ChatPane, Markdown(KaTeX),
  shadcn ui, api/chatStream 골격.
- 신규/확장:
  - **트랙 네비**: 4트랙(paper/prereq/landscape/trends) 탭 전환. 트랙별 진행률.
  - **prereq 과목 그룹 뷰**: `track.groups`로 챕터를 과목 단위로 묶어 표시,
    교재명(group.textbook) 노출.
  - **known 토글**: prereq 챕터(또는 그룹)별 "이미 안다" 체크 → C3 호출 →
    curriculum 갱신. known 챕터는 done 처리/스킵 표시.
  - **소스 인용 표시**: landscape/trends 챕터의 sourceIds → curriculum.sources
    메타(제목/연도/링크) 표시.
  - 채팅: C2 NDJSON 스트림 소비(token 누적, concept_done/mastered/done 반영).
    chat 요청 body에 trackId/chapterId 포함.
- `lib/api.ts`: C2(chatStream), C3(known), C4(getSession), activate, pass, 업로드.
- `types.ts`: Curriculum/Track/Chapter/Source/Message를 SSOT 모양 그대로 미러.

경계:
- 모든 API 호출은 C2/C3/C4 + activate/pass 계약 준수.
- 자료구조 타입은 `docs/WORKSTREAMS.md`의 모양과 1:1.

검증:
- `npm run build`(타입체크 포함) 통과.
- mock 데이터로 4트랙 전환, prereq known 토글, 소스 표시, 채팅 스트림(가짜 NDJSON)
  이 동작하는 화면을 확인.

보고: 구현한 화면/컴포넌트 + mock→실제 API 전환 시 바꿔야 할 지점.
