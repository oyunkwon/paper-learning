"""튜터 루프 (트랙 인식): 채팅 컨텍스트 조립 → 응답 스트리밍 → 진행신호 파싱.

learning `tutor.py`에서 검증된 sentinel 파싱·루프가드·자문자답가드를 **그대로
포팅**하고, paper-learning의 멀티트랙(DESIGN.md D5)으로만 확장했다. 두 가지가
learning과 다르다:

  1. mutation은 단일 챕터 리스트가 아니라 (track, chapter)로 주소가 매겨진
     커리큘럼에 적용된다. 그래서 `app.runtime`의 chapter-scoped 함수에 chapter
     dict를 직접 넘긴다 (sentinel은 chapterId만 들고, track은 호출자가 컨텍스트로
     안다 — WORKSTREAMS C1).
  2. `build_messages`가 트랙 kind별로 컨텍스트를 다르게 주입한다:
       prereq    : 자료 페이지 없음. 정준 교재 지식 기반 소크라테스.
       paper     : 논문 원문 페이지(pageStart/End) 주입 (learning과 동일).
       landscape : chapter.sourceIds -> curriculum.sources lookup -> "참고 소스"
       trends      텍스트 블록 주입 (WORKSTREAMS C5). 논문 페이지는 안 씀.

sentinel 문법·진행신호 의미는 learning과 동일하다(트랙 추가만). stream_turn이
yield하는 프레임은 WORKSTREAMS C2 스키마와 정확히 일치한다.
"""

from __future__ import annotations

import re
from typing import Any, AsyncIterator, Awaitable, Callable, Protocol, runtime_checkable

from app import material, runtime
from app.llm import LLMClient, Message
from app.tutor_prompts import system_for_track


@runtime_checkable
class SessionLike(Protocol):
    """튜터가 자료를 읽기 위해 필요한 세션의 최소 표면. W1 SessionStore의
    Session 데이터클래스가 이 모양을 만족한다(스토어 자체엔 의존하지 않음)."""

    kind: str

    @property
    def material_path(self) -> Any: ...

    @property
    def pages_dir(self) -> Any: ...


# Sentinels. learning 그대로: 공백에 관대하고, 키워드는 대소문자 무시.
_MASTERED_RE = re.compile(r"<<\s*MASTERED\s*:\s*([A-Za-z0-9_-]+)\s*>>", re.IGNORECASE)
_CONCEPT_RE = re.compile(
    r"<<\s*CONCEPT_DONE\s*:\s*([A-Za-z0-9_-]+)\s*:\s*(\d+)\s*>>", re.IGNORECASE
)
# 자가치유 위치 마커: "지금 이 챕터의 개념 N을 가르치는 중." N에 도달했다는 건
# 0..N-1이 이미 학습·통과됐다는 뜻이다. 매 응답마다 재선언되므로 마커 하나가
# 누락돼도 다음 턴에서 자동 보정된다. 뒤따르는 개행을 함께 소비해 첫 줄을
# 제거해도 빈 줄이 남지 않게 한다.
_TEACHING_RE = re.compile(
    r"<<\s*TEACHING\s*:\s*([A-Za-z0-9_-]+)\s*:\s*(\d+)\s*>>[ \t]*\n?", re.IGNORECASE
)

# 퇴행 루프 가드. 일부 모델은 같은 짧은 줄을 무한 반복한다("맞아요? 다음으로 ㄱㄱ"
# xN). max_tokens 캡이 없으면 타임아웃까지 턴을 도배한다. 같은 비어있지 않은
# 정규화 줄이 연속으로 이 횟수만큼 반복되면 스트림을 끊고, 반복 시작 직전까지의
# 텍스트로 턴을 마무리한다.
_LOOP_REPEAT_LIMIT = 4

# 자문자답 가드. 튜터는 질문하고 STOP, 실제 학습자를 기다려야 한다. 모델이 이를
# 무시하고 가상의 학습자 턴(보통 "user", "학습자:", "assistant", "튜터:" 같은
# 화자 레이블로 시작)을 만들어 혼자 대화를 이어가는 경우가 있다. 그런 레이블로
# 시작하는 줄을 감지해 그 줄부터 전부 잘라, 진짜 튜터 턴(질문까지)만 클라이언트에
# 도달하게 한다. 레이블은 단독 줄이거나 ":" / 내용이 뒤따를 수 있다.
_SPEAKER_LABEL_RE = re.compile(
    r"^\s*(?:user|assistant|human|ai|tutor|student"
    r"|학습자|학생|튜터|선생(?:님)?|사용자|질문자)\s*[:：]?\s*$"
    r"|^\s*(?:user|assistant|human|ai|tutor|student"
    r"|학습자|학생|튜터|선생(?:님)?|사용자|질문자)\s*[:：]\s+",
    re.IGNORECASE,
)


def build_messages(
    session: SessionLike,
    curriculum: dict[str, Any],
    track: dict[str, Any],
    chapter: dict[str, Any],
    thread: list[dict[str, Any]],
) -> list[Message]:
    """한 챕터에 대한 튜터 컨텍스트를 조립한다: 시스템(트랙별) + 트랙/커리큘럼
    개요 + 트랙 kind별 근거 자료 + 그 챕터의 기존 스레드.

    ``thread``는 그 챕터의 이전 메시지로, async 호출자가 미리 가져와 넘긴다(이
    함수는 sync). 입력은 모두 dict/Session 표면이며 SessionStore에 의존하지
    않는다.
    """
    track_id = track.get("id", "")
    messages: list[Message] = [
        {"role": "system", "content": system_for_track(track_id)}
    ]

    overview = _curriculum_overview(curriculum, track, chapter)
    context_blocks: list[dict[str, Any]] = [material.text_block(overview)]

    # 트랙 kind별 근거 자료 주입.
    if track_id == "paper":
        context_blocks.extend(_paper_material_blocks(session, chapter))
    elif track_id in ("landscape", "trends"):
        block = _sources_block(curriculum, chapter)
        if block:
            context_blocks.append(material.text_block(block))
    # prereq(dependency): 별도 근거 자료 없음. 시스템 프롬프트의 정준 교재
    # 지식으로 가르친다.

    messages.append({"role": "user", "content": context_blocks})
    messages.append(
        {
            "role": "assistant",
            "content": "알겠어. 커리큘럼과 자료를 확인했어. 이 챕터를 진행할게.",
        }
    )

    for m in thread:
        role = m.get("role")
        content = m.get("content")
        if role in ("user", "assistant") and isinstance(content, str):
            messages.append({"role": role, "content": content})

    return messages


def _paper_material_blocks(
    session: SessionLike, chapter: dict[str, Any]
) -> list[dict[str, Any]]:
    """paper 트랙: 논문 원문 페이지를 주입한다 (learning과 동일).

    텍스트 레이어를 우선한다(이미지의 ~1/100 무게라 챕터 전체가 잘림 없이 한
    요청에 들어간다). 텍스트가 없는 스캔 PDF면 페이지 이미지로 폴백하되 payload
    예산까지만 보낸다."""
    if session.kind != "pdf":
        return []
    blocks: list[dict[str, Any]] = []
    page_text = ""
    try:
        page_text = material.pdf_page_text(
            session.material_path,
            start=chapter.get("pageStart"),
            end=chapter.get("pageEnd"),
        )
    except material.MaterialError:
        page_text = ""
    if page_text:
        blocks.append(
            material.text_block(
                "현재 챕터에 해당하는 논문 원문(텍스트):\n\n" + page_text
            )
        )
    else:
        page_blocks, _lo, _hi = material.pdf_page_blocks_capped(
            session.pages_dir,
            start=chapter.get("pageStart"),
            end=chapter.get("pageEnd"),
        )
        if page_blocks:
            blocks.append(material.text_block("현재 챕터에 해당하는 논문 페이지:"))
            blocks.extend(page_blocks)
    return blocks


def _sources_block(
    curriculum: dict[str, Any], chapter: dict[str, Any]
) -> str:
    """landscape/trends 트랙: chapter.sourceIds를 curriculum.sources에서 lookup해
    "참고 소스" 텍스트 블록을 만든다 (WORKSTREAMS C5).

    소스는 retrieve된 검증 메타이므로 그대로 인용 가능하다. 연결된 소스가 없으면
    빈 문자열을 돌려준다(블록 미주입)."""
    source_ids = chapter.get("sourceIds") or []
    if not source_ids:
        return ""
    by_id = {s.get("id"): s for s in curriculum.get("sources", []) if isinstance(s, dict)}
    lines: list[str] = [
        "이 챕터의 참고 소스 (실제 학술 API에서 검증·retrieve됨. 아래 범위 안에서만",
        "인용하고, 목록에 없는 논문·저자·DOI를 새로 지어내지 마라):",
        "",
    ]
    found = 0
    for sid in source_ids:
        s = by_id.get(sid)
        if not isinstance(s, dict):
            continue
        found += 1
        lines.append(_format_source(s, found))
    if found == 0:
        return ""
    return "\n".join(lines)


def _format_source(s: dict[str, Any], n: int) -> str:
    """소스 메타 한 건을 사람이 읽을 수 있는 항목으로 포맷한다."""
    parts: list[str] = [f"{n}. {s.get('title', '(제목 없음)')}"]
    authors = s.get("authors") or []
    meta_bits: list[str] = []
    if authors:
        shown = ", ".join(str(a) for a in authors[:3])
        if len(authors) > 3:
            shown += " 외"
        meta_bits.append(shown)
    if s.get("year"):
        meta_bits.append(str(s["year"]))
    if s.get("venue"):
        meta_bits.append(str(s["venue"]))
    if meta_bits:
        parts.append("   " + " · ".join(meta_bits))
    if s.get("abstract"):
        parts.append(f"   요약: {s['abstract']}")
    elif s.get("summary"):
        parts.append(f"   요약: {s['summary']}")
    link = s.get("doi") or s.get("arxivId") or s.get("url")
    if link:
        parts.append(f"   출처: {link}")
    return "\n".join(parts)


def _curriculum_overview(
    curriculum: dict[str, Any],
    track: dict[str, Any],
    chapter: dict[str, Any] | None,
) -> str:
    """현재 트랙의 챕터 목록 + 지금 가르칠 챕터의 개념 순서를 텍스트로 만든다.

    learning과 달리 커리큘럼 전체가 아니라 **현재 트랙**의 챕터만 나열한다. 이
    대화는 한 트랙 안에서만 진행되기 때문이다 (sentinel도 chapterId만 들고
    있다)."""
    paper = curriculum.get("paper", {})
    title = paper.get("title", "") if isinstance(paper, dict) else ""
    lines = [
        f"# 논문: {title}",
        f"## 트랙: {track.get('title', track.get('id', ''))}",
    ]
    if track.get("summary"):
        lines.append(str(track["summary"]))
    lines.append("")
    for ch in track.get("chapters", []):
        marker = {"done": "[완료]", "active": "[진행중]", "locked": "[잠김]"}.get(
            ch.get("status", "locked"), ""
        )
        if ch.get("known"):
            marker = "[이미앎]"
        lines.append(f"- {marker} {ch.get('id')}: {ch.get('title')}")
    lines.append("")
    if chapter is not None:
        lines.append(
            f"## 지금 가르칠 챕터: {chapter.get('id')} - {chapter.get('title')}"
        )
        if chapter.get("summary"):
            lines.append(str(chapter["summary"]))
        concepts = chapter.get("concepts", [])
        done = chapter.get("conceptsDone", [])
        if concepts:
            lines.append("개념 순서 (의존성 순):")
            for i, c in enumerate(concepts):
                tick = "v" if i < len(done) and done[i] else " "
                lines.append(f"  [{tick}] {i}. {c}")
        lines.append("")
        lines.append(
            "이 대화는 오직 위 챕터만 다룬다. 이 챕터의 마지막 개념까지 학습자가 "
            "이해하면 <<MASTERED:챕터ID>>를 출력하고, 다음 챕터로 넘어가자고 "
            "안내하되 다음 챕터 내용을 미리 설명하지는 마라."
        )
    else:
        lines.append("## 이 트랙의 모든 챕터를 완료했다. 학습자가 질문하면 근거에 맞춰 답하라.")
    return "\n".join(lines)


async def stream_turn(
    session: SessionLike,
    curriculum: dict[str, Any],
    track: dict[str, Any],
    chapter: dict[str, Any],
    thread: list[dict[str, Any]],
    client: LLMClient,
    *,
    model: str,
    on_curriculum_change: Callable[[dict[str, Any]], Awaitable[None]],
) -> AsyncIterator[dict[str, Any]]:
    """특정 (트랙, 챕터)에 대한 튜터 턴을 구조화된 프레임으로 스트리밍한다.

    yield 프레임은 WORKSTREAMS C2 스키마와 정확히 일치한다:
      {"type": "token", "text": ...}                       보이는 텍스트
      {"type": "concept_done", "chapterId": ..., "index"}  개념 통과
      {"type": "mastered", "chapterId": ...}               챕터 완료
      {"type": "done", "content": <전체 가시 텍스트>}        최종

    mutation은 sentinel에 담긴 chapterId가 아니라 호출자가 넘긴 `chapter` dict에
    `app.runtime`의 chapter-scoped 함수로 직접 적용한다. (트랙은 컨텍스트로 알고
    sentinel은 chapterId만 들고 있다 — C1.) curriculum이 바뀌면
    on_curriculum_change(curriculum)을 await한다.
    """
    messages = build_messages(session, curriculum, track, chapter, thread)
    buffer = ""          # 부분 sentinel 꼬리를 품을 수 있는 텍스트
    visible_parts: list[str] = []
    changed = False
    guard = _LoopGuard(_LOOP_REPEAT_LIMIT)
    speaker = _SpeakerLabelGuard()
    cut = False

    async for delta in client.stream(model=model, messages=messages, temperature=0.4):
        buffer += delta
        # 지금까지 발견된 완성 sentinel을 적용 (chapter dict를 mutate).
        buffer, frames, did_change = _drain_sentinels(buffer, chapter)
        changed = changed or did_change
        for f in frames:
            yield f
        # 부분 "<<..." 꼬리를 안전하게 지난 텍스트만 방출.
        safe, buffer = _split_safe(buffer)
        if safe:
            emit, hit = guard.feed(safe)
            # 튜터가 학습자를 연기하지 못하게: 가상 화자 턴을 클라이언트 도달 전 컷.
            if emit:
                emit, spk_hit = speaker.feed(emit)
            else:
                spk_hit = False
            if emit:
                visible_parts.append(emit)
                yield {"type": "token", "text": emit}
            if hit or spk_hit:
                # 루프/자문자답 감지. 남은 스트림(과 보류 버퍼)을 버리고 깔끔히 종료.
                cut = True
                buffer = ""
                break

    if cut:
        # 루프/자문자답 감지: 보류 버퍼나 guard pending을 flush하지 않는다
        # (폭주/조작된 꼬리이므로). 바로 턴 종료로 간다.
        if changed:
            await on_curriculum_change(curriculum)
        yield {"type": "done", "content": "".join(visible_parts).strip()}
        return

    # 남은 것 flush (최종 sentinel이 맨 끝에 있을 수 있음).
    buffer, frames, did_change = _drain_sentinels(buffer, chapter)
    changed = changed or did_change
    for f in frames:
        yield f
    tail = guard.flush()
    if tail:
        tail, _ = speaker.feed(tail)
    tail += speaker.flush()
    if tail:
        buffer += tail
    if buffer:
        visible_parts.append(buffer)
        yield {"type": "token", "text": buffer}

    if changed:
        await on_curriculum_change(curriculum)

    yield {"type": "done", "content": "".join(visible_parts).strip()}


def _drain_sentinels(
    text: str, chapter: dict[str, Any]
) -> tuple[str, list[dict[str, Any]], bool]:
    """완성된 sentinel을 찾아 제거하고, mutation을 적용하고, 프레임을 돌려준다.

    learning과 달리 mutation 대상은 호출자가 넘긴 `chapter` dict 하나다. sentinel의
    chapterId는 프레임에 **파싱된 그대로** 싣는다(C2). 정상 흐름에선 그 chapterId가
    현재 챕터와 일치한다."""
    frames: list[dict[str, Any]] = []
    changed = False

    def _concept(m: re.Match[str]) -> str:
        nonlocal changed
        cid, idx = m.group(1), int(m.group(2))
        if runtime.mark_concept_done(chapter, idx):
            changed = True
            frames.append({"type": "concept_done", "chapterId": cid, "index": idx})
        return ""

    def _teaching(m: re.Match[str]) -> str:
        # "지금 개념 N을 가르친다" => 0..N-1은 완료. 자가치유: 이전 턴에서 모델이
        # 빠뜨린 마커를 backfill한다.
        nonlocal changed
        cid, idx = m.group(1), int(m.group(2))
        for done_idx in runtime.mark_concepts_before(chapter, idx):
            changed = True
            frames.append(
                {"type": "concept_done", "chapterId": cid, "index": done_idx}
            )
        return ""

    def _mastered(m: re.Match[str]) -> str:
        nonlocal changed
        cid = m.group(1)
        if runtime.mark_chapter_done(chapter):
            changed = True
            frames.append({"type": "mastered", "chapterId": cid})
        return ""

    text = _TEACHING_RE.sub(_teaching, text)
    text = _CONCEPT_RE.sub(_concept, text)
    text = _MASTERED_RE.sub(_mastered, text)
    return text, frames, changed


class _LoopGuard:
    """같은 짧은 줄을 무한 반복하는 모델을 감지해 잘라낸다.

    보이는 텍스트 델타를 순서대로 받아 개행으로 나누고, 완성된 각 줄의 정규화
    형태를 직전 줄과 비교한다. 같은 비어있지 않은 줄이 연속 ``limit``번 반복되면
    ``feed``가 ``looped=True``를 돌려주고, 방출 텍스트는 반복 꼬리가 클라이언트에
    닿지 않게 잘린다. (learning 그대로.)
    """

    def __init__(self, limit: int) -> None:
        self._limit = max(2, limit)
        self._pending = ""          # 아직 개행 안 된 현재 줄
        self._last_norm: str | None = None
        self._run = 0               # _last_norm의 연속 반복 횟수

    def feed(self, text: str) -> tuple[str, bool]:
        """(방출가능_텍스트, looped) 반환. 루프 시 방출 텍스트엔 문제의 반복 직전
        까지만 담겨, 처음 몇 번은 보이고 폭주 꼬리는 잘린다."""
        out: list[str] = []
        i = 0
        n = len(text)
        while i < n:
            nl = text.find("\n", i)
            if nl == -1:
                self._pending += text[i:]
                break
            line = self._pending + text[i : nl + 1]
            self._pending = ""
            i = nl + 1
            norm = line.strip()
            if not norm:
                # 빈 줄은 run을 끊지 않는다(반복은 종종 빈 줄로 구분됨); 통과.
                out.append(line)
                continue
            if norm == self._last_norm:
                self._run += 1
            else:
                self._last_norm = norm
                self._run = 1
            if self._run >= self._limit:
                # 이 반복을 방출하기 전에 컷. 앞선 발생은 이미 나갔다; 이번과 이후를 버림.
                self._pending = ""
                return "".join(out), True
            out.append(line)
        return "".join(out), False

    def flush(self) -> str:
        """개행으로 끝나지 않은 마지막 부분 줄을 돌려준다. 스트림 끝에서 한 번만
        호출(루프가 감지되지 않았을 때만)."""
        rest, self._pending = self._pending, ""
        return rest


class _SpeakerLabelGuard:
    """튜터가 학습자를 연기하기 시작하면 스트림을 자른다.

    튜터는 질문하고 멈춰야 한다. 대신 학습자 답을 조작할 때, 그 환각 턴은 거의
    항상 화자 레이블("user", "학습자:", "assistant", "튜터:", ...)로 시작한다.
    보이는 텍스트를 순서대로 받아 그런 레이블로 시작하는 첫 줄에서 ``hit=True``를
    돌려주고 그 줄 앞 텍스트만 방출한다 — 질문까지의 진짜 튜터 턴. 이후는 버림.
    (learning 그대로.)
    """

    def __init__(self) -> None:
        self._pending = ""  # 아직 개행 안 된 현재 줄

    def feed(self, text: str) -> tuple[str, bool]:
        """(방출가능_텍스트, hit) 반환. hit 시 방출엔 문제의 화자레이블 줄 앞
        텍스트만 담긴다."""
        out: list[str] = []
        i = 0
        n = len(text)
        while i < n:
            nl = text.find("\n", i)
            if nl == -1:
                # 완성된 줄(개행 포함)에서만 매칭. "user"로 시작하는 부분 조각은
                # 정상 단어("user 입력")의 시작일 수 있으니 잘릴 위험 대신 보류.
                self._pending += text[i:]
                break
            line = self._pending + text[i : nl + 1]
            self._pending = ""
            i = nl + 1
            if _SPEAKER_LABEL_RE.match(line):
                return "".join(out), True
            out.append(line)
        return "".join(out), False

    def flush(self) -> str:
        rest, self._pending = self._pending, ""
        # 보류된 마지막 줄(개행 없음)도 레이블일 수 있다.
        if _SPEAKER_LABEL_RE.match(rest):
            return ""
        return rest


def _split_safe(buffer: str) -> tuple[str, str]:
    """(방출가능, 보류)로 나눈다. 부분 sentinel 꼬리를 붙잡아 반쪽 sentinel이
    클라이언트로 새어나가지 않게 한다.

    sentinel은 `<<...>>`이다. 닫는 `>>`가 뒤 토큰에 올 수 있으므로, 첫 `>`가
    나타나도 닫히지 않은 `<<`부터는 계속 붙잡아야 한다. (learning 그대로.)"""
    idx = buffer.rfind("<<")
    if idx != -1 and ">>" not in buffer[idx:]:
        return buffer[:idx], buffer[idx:]
    if buffer.endswith("<"):
        return buffer[:-1], buffer[-1:]
    return buffer, ""
