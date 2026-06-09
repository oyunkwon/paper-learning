# 작업 분할 + 인터페이스 계약 (SSOT)

병렬 작업을 위한 단일 진실 소스. 모든 워크스트림은 이 계약을 지킨다. 계약을
바꾸려면 여기를 먼저 고치고 영향받는 워크스트림에 알린다.

설계 배경은 `docs/DESIGN.md`. 이 문서는 **경계면(인터페이스)**만 다룬다.

## 현재 완료된 자산 (이미 머지됨, 변경 금지 없이 사용)

- `app/config.py` — 설정 (LLM/retrieval/DB)
- `app/llm.py` — 멀티모달+스트리밍 LLM 클라이언트
- `app/material.py` — PDF 렌더/텍스트/배칭
- `app/jsonparse.py` — 관대한 JSON 파서
- `app/schema.py` — 멀티트랙 커리큘럼 정규화/검증
- `app/runtime.py` — 트랙 인식 진행신호 mutation + known 토글
- `app/retrieval/*` — S2/OpenAlex/arXiv 클라이언트 + 식별 (검증됨)
- `app/planner/*` — plan_paper 파이프라인 (검증됨, 4트랙 생성)

## 핵심 자료구조 (확정)

### Curriculum (sessions.curriculum JSONB)
```jsonc
{
  "paper":   { "title", "authors":[], "arxivId", "doi", "year", "abstract", "venue" },
  "tracks":  [ Track, ... ],          // 순서: paper, prereq, landscape, trends
  "graph":   { "nodes":[], "edges":[] },
  "sources": [ Source, ... ]
}
```

### Track
```jsonc
{
  "id": "prereq|landscape|trends|paper",
  "kind": "dependency|reading",
  "title": "...", "summary": "...",
  "groups": [ { "id", "title", "textbook", "chapterIds":[] } ],  // prereq만
  "chapters": [ Chapter, ... ]
}
```

### Chapter
```jsonc
{
  "id": "ch1", "title": "...", "summary": "...",
  "concepts": ["..."], "conceptsDone": [false],   // 런타임
  "status": "active|locked|done",                 // 런타임
  "pageStart": null, "pageEnd": null,             // paper 트랙만 의미
  "sourceIds": ["..."],                           // landscape/trends grounding
  "known": false                                  // prereq만 (학습자 토글)
}
```

### Source
```jsonc
{ "id", "type":"paper|survey|textbook", "title", "authors":[],
  "url", "doi", "arxivId", "year", "venue", "retrievedFrom" }
```

## 합의된 인터페이스 계약

### C1. sentinel = chapter만, track은 요청에 (확정)
- 튜터 sentinel은 learning 그대로: `<<TEACHING:chapterId:idx>>`,
  `<<CONCEPT_DONE:chapterId:idx>>`, `<<MASTERED:chapterId>>`.
- 어느 트랙인지는 **chat 요청 body의 `trackId`**로 전달. 백엔드는 (trackId,
  chapterId)로 챕터를 찾는다 (`runtime.find_chapter(curriculum, trackId, chapterId)`).

### C2. Chat API (확정)
```
POST /api/sessions/{id}/chat
body: { "trackId": str, "chapterId": str,
        "message"?: str, "kickoff"?: bool, "pass"?: bool }
resp: NDJSON 스트림, 한 줄당 한 프레임:
  {"type":"token","text": str}
  {"type":"concept_done","chapterId": str,"index": int}
  {"type":"mastered","chapterId": str}
  {"type":"done","content": str}        // 최종, 전체 가시 텍스트
  {"type":"error","message": str}
```
- `kickoff`: 챕터 첫 진입(synthetic user turn 주입).
- `pass`: 현재 개념 건너뛰기(서버가 먼저 /pass로 marked, 그다음 chat).
- 프레임은 learning과 동일 + chapterId는 sentinel에서 파싱된 그대로.

### C3. Known 토글 API (확정)
```
POST /api/sessions/{id}/known
body: { "trackId": str, "chapterId": str, "known": bool }
resp: 업데이트된 curriculum (전체)
```
- 구현은 `runtime.set_known(chapter, known)` 호출 후 curriculum 저장.

### C4. 세션 조회 응답 (확정)
```
GET /api/sessions/{id}
resp: {
  ...summary,                  // id,title,filename,kind,createdAt,status,progress
  "curriculum": Curriculum | null,
  "threads": { "<trackId>": { "<chapterId>": [Message, ...] } }   // 2단계 중첩
}
```
- Message = `{ "role":"user|assistant", "content": str, "ts": float, "synthetic"?: bool }`

### C5. 튜터의 소스 주입 (명시, 합의 불필요)
- reading 트랙(landscape/trends) 챕터의 `sourceIds[]` → curriculum `sources[]`에서
  lookup → 튜터 컨텍스트에 "참고 소스" 블록으로 주입. W2 내부 책임.

### Activate API (learning 차용, track 추가)
```
POST /api/sessions/{id}/activate
body: { "trackId": str, "chapterId": str }
resp: 업데이트된 curriculum
```

### Pass API (learning 차용, track 추가)
```
POST /api/sessions/{id}/pass
body: { "trackId": str, "chapterId": str }
resp: { "curriculum", "passedIndex": int|null, "chapterDone": bool }
```

---

## W1 — DB / 세션 레이어  (의존: 없음, 즉시 시작)

**산출물:** `app/db.py`, `app/models.py`, `app/sessions.py`, `app/alembic/*`

- `db.py`: learning에서 차용 (async SQLAlchemy 엔진 + session_scope). 거의 그대로.
- `models.py`:
  - `SessionRow`: id, title, filename, kind, page_count, curriculum(JSONB),
    status, planning_error, progress_done, progress_total, created_at, updated_at.
    **인증/user는 생략** (로컬 단일 유저). user_id 컬럼 없이 시작.
  - `Thread`: PK (session_id, **track_id**, chapter_id), messages(JSONB).
    → C4의 2단계 중첩을 DB에서 표현. (learning은 (session,chapter)였음 — track 추가)
- `sessions.py`: `Session` 데이터클래스(material_path/pages_dir 경로 계산) +
  `SessionStore` (async):
  - lifecycle: `create`, `get`, `list`, `delete`
  - planning: `set_progress`, `mark_ready`, `mark_error`, `fail_stuck_planning`
  - material: `save_material`, `save_pages`, `ensure_cached` (R2 없이 로컬 캐시만)
  - curriculum: `read_curriculum`, `write_curriculum`
  - threads: `read_thread(id, track, chapter)`, `append_to_thread(id, track,
    chapter, role, content, synthetic=)`, `all_threads(id)` → C4 2단계 dict 반환
  - **스토리지는 로컬 디스크만** (R2 생략). `material_path`/`pages_dir`는
    `config.DEFAULT_DATA_DIR` 아래.

**경계:** SessionStore의 공개 메서드 시그니처가 W3와의 계약. 위 목록 고정.
threads 반환은 반드시 C4 모양.

## W2 — 튜터 루프 (트랙 인식)  (의존: 없음, 즉시 시작)

**산출물:** `app/tutor.py`, `app/tutor_prompts.py`

- learning `tutor.py` 포팅:
  - sentinel 파싱(`_drain_sentinels`), 루프가드(`_LoopGuard`), 자문자답
    가드(`_SpeakerLabelGuard`), `_split_safe` → **그대로 가져옴**.
  - `stream_turn(session, curriculum, track, chapter, thread, client, *, model,
    on_curriculum_change)` → 트랙을 인자로 받음. runtime mutation은
    `app.runtime`의 chapter-scoped 함수 사용 (chapter dict를 직접 넘김).
  - `build_messages`: 트랙 kind별 분기.
    - `prereq`(dependency): 소크라테스 튜터. 자료 페이지 없음(교재 기반). 개념
      순서대로 설명+질문.
    - `paper`(reading): 논문 원문 페이지(pageStart/End) 주입 + 원문 인용 강조.
      learning 방식과 가장 가까움.
    - `landscape`/`trends`(reading): chapter.sourceIds → curriculum.sources에서
      메타 lookup → "참고 소스" 텍스트 블록 주입 (C5). 논문 페이지는 안 씀.
- `tutor_prompts.py`: TUTOR_SYSTEM 트랙별 변형. learning의 진행신호 규칙
  (`<<TEACHING>>`/`<<CONCEPT_DONE>>`/`<<MASTERED>>`)은 모든 트랙 공통 유지.

**경계:** `stream_turn`이 yield하는 프레임은 **C2 프레임 스키마와 정확히 일치**.
입력은 curriculum/track/chapter/thread dict (스토어 비의존). on_curriculum_change는
async 콜백.

## W3 — FastAPI 라우트 + 백그라운드 플래닝  (의존: W1, W2)

**산출물:** `app/main.py`

- learning `main.py` 골격 차용, 인증/R2 제거.
- 라우트: health, create_session(업로드→백그라운드 _run_planning), list, get(C4),
  rename, delete, material 서빙, curriculum, **chat(C2)**, **activate**, **pass**,
  **known(C3)**.
- `_run_planning`: PDF 렌더 → `planner.plan_paper`(CachedHTTP 생성/주입) →
  `store.mark_ready`. learning의 백그라운드 task 패턴 + progress 폴링 그대로.
- chat 핸들러: body에서 trackId/chapterId → `runtime.find_chapter` →
  `tutor.stream_turn` → NDJSON.

**경계:** W1 SessionStore 시그니처 + W2 stream_turn 시그니처에 의존. 그 전까지
stub로 시작 가능하지만 권장 순서상 W1·W2 후 착수.

## W4 — 프론트엔드  (의존: API 계약 C2/C3/C4, mock으로 병렬 가능)

**산출물:** `frontend/*` (learning UI 차용 + 멀티트랙 확장)

- learning 컴포넌트 차용: SessionSidebar, UploadZone, ChatPane, Markdown(KaTeX).
- 신규:
  - **트랙 탭/네비**: 4트랙(paper/prereq/landscape/trends) 전환.
  - **prereq known 토글**: 각 챕터(또는 과목 그룹) 체크해제 → C3 호출.
  - **과목 그룹 뷰**: prereq 트랙은 groups로 묶어 표시 (교재명 노출).
  - **소스 표시**: landscape/trends 챕터의 sourceIds → sources 메타(제목/연도/
    링크) 인용 표시.
  - 진행률: 트랙별 progress (runtime.progress 반환 모양).
- `lib/api.ts`: C2(chatStream NDJSON), C3, C4, activate/pass 호출.
- `types.ts`: 위 자료구조 미러.

**경계:** API 계약(C2/C3/C4 + activate/pass)만 지키면 백엔드 없이 mock 데이터로
병렬 개발 가능.

---

## 착수 순서 (확정)
- **즉시 병렬**: W1, W2, W4
- **W1·W2 완료 후**: W3 (둘의 시그니처에 의존)
- 통합 지점: W3가 W1 store + W2 stream_turn을 실제 연결. W4는 mock → 실제 API 전환.
