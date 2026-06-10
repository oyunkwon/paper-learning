"""튜터 루프 단위 테스트 (네트워크 없음).

FakeLLMClient로 client.stream을 흉내내어 stream_turn을 구동하고:
  (a) sentinel이 제거된 깨끗한 token이 나오는지,
  (b) concept_done / mastered 프레임이 C2 스키마대로 나오는지,
  (c) 자문자답/퇴행 루프가 컷되는지,
  (d) 트랙별 build_messages가 알맞은 컨텍스트(소스 블록 등)를 넣는지 확인한다.
"""

from __future__ import annotations

from typing import Any, AsyncIterator

import pytest

from app import tutor
from app.schema import normalize_curriculum
from app.tutor_prompts import (
    TUTOR_SYSTEM_LANDSCAPE,
    TUTOR_SYSTEM_PAPER,
    TUTOR_SYSTEM_PREREQ,
    TUTOR_SYSTEM_TRENDS,
)


# --- fakes ------------------------------------------------------------------


class FakeLLMClient:
    """client.stream을 흉내낸다: 미리 정한 청크 리스트를 델타로 흘린다.
    LLMClient.stream과 동일 시그니처(키워드 model/messages/temperature)."""

    def __init__(self, chunks: list[str]) -> None:
        self._chunks = chunks
        self.last_messages: list[dict[str, Any]] | None = None
        self.last_stop: list[str] | None = None

    async def stream(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        temperature: float = 0.4,
        max_tokens: int | None = None,
        stop: list[str] | None = None,
    ) -> AsyncIterator[str]:
        self.last_messages = messages
        self.last_stop = stop
        for c in self._chunks:
            yield c


class FakeSession:
    """튜터가 읽는 세션 표면(SessionLike)의 최소 가짜. paper 트랙 자료 경로는
    이 테스트에서 건드리지 않도록 kind를 비-pdf로 둔다."""

    def __init__(self, kind: str = "md") -> None:
        self.kind = kind

    @property
    def material_path(self) -> Any:
        raise AssertionError("이 테스트는 자료 파일을 읽지 않아야 한다")

    @property
    def pages_dir(self) -> Any:
        raise AssertionError("이 테스트는 자료 파일을 읽지 않아야 한다")


def _curriculum() -> dict[str, Any]:
    raw = {
        "paper": {"title": "Attention Is All You Need", "year": 2017},
        "tracks": [
            {
                "id": "paper",
                "chapters": [
                    {"id": "c1", "title": "주장", "concepts": ["self-attention", "multi-head"],
                     "pageStart": 1, "pageEnd": 4},
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
                ],
            },
            {
                "id": "landscape",
                "chapters": [
                    {"id": "l1", "title": "RNN 계열", "concepts": ["seq2seq"],
                     "sourceIds": ["s_rnn"]},
                ],
            },
            {
                "id": "trends",
                "chapters": [
                    {"id": "t1", "title": "BERT 이후", "concepts": ["pretraining"],
                     "sourceIds": ["s_bert"]},
                ],
            },
        ],
        "sources": [
            {"id": "s_rnn", "title": "Sequence to Sequence Learning",
             "authors": ["Sutskever", "Vinyals", "Le"], "year": 2014,
             "abstract": "seq2seq with LSTM encoder-decoder.",
             "retrievedFrom": "openalex", "doi": "10.0/seq2seq"},
            {"id": "s_bert", "title": "BERT", "authors": ["Devlin"], "year": 2018,
             "abstract": "Bidirectional pretraining.", "retrievedFrom": "openalex"},
        ],
    }
    return normalize_curriculum(raw)


async def _run(chunks: list[str], track_id: str, chapter_id: str):
    """stream_turn을 끝까지 돌려 (프레임 리스트, 변경된 커리큘럼)을 돌려준다."""
    curr = _curriculum()
    track = next(t for t in curr["tracks"] if t["id"] == track_id)
    chapter = next(c for c in track["chapters"] if c["id"] == chapter_id)
    changes: list[dict[str, Any]] = []

    async def on_change(c: dict[str, Any]) -> None:
        changes.append(c)

    client = FakeLLMClient(chunks)
    frames = [
        f
        async for f in tutor.stream_turn(
            FakeSession(),
            curr,
            track,
            chapter,
            [],
            client,  # type: ignore[arg-type]
            model="m",
            on_curriculum_change=on_change,
        )
    ]
    return frames, curr, chapter, changes, client


def _tokens(frames: list[dict[str, Any]]) -> str:
    return "".join(f["text"] for f in frames if f["type"] == "token")


def _done_content(frames: list[dict[str, Any]]) -> str:
    done = [f for f in frames if f["type"] == "done"]
    assert len(done) == 1
    return done[0]["content"]


# --- (a) sentinel 제거 + 깨끗한 token ---------------------------------------


async def test_teaching_sentinel_stripped_from_visible_text():
    chunks = [
        "<<TEACHING:p1:0>>\n",
        "기저는 ", "벡터공간을 생성하는 ", "독립 벡터들의 모임이야. ",
        "질문: $\\mathbb{R}^2$의 기저 하나를 말해볼래?",
    ]
    frames, _curr, _ch, _changes, _client = await _run(chunks, "prereq", "p1")
    text = _tokens(frames)
    assert "<<TEACHING" not in text
    assert "기저는 벡터공간을 생성하는" in text
    # done 프레임의 content도 깨끗해야 한다.
    assert "<<" not in _done_content(frames)


# --- (b) concept_done / mastered 프레임 (C2 스키마) -------------------------


async def test_concept_done_and_mastered_frames():
    chunks = [
        "<<TEACHING:p1:1>>\n",
        "차원은 기저의 크기야. 이해했지? 좋아.\n",
        "<<CONCEPT_DONE:p1:1>>\n",
        "<<MASTERED:p1>>",
    ]
    frames, curr, chapter, changes, _client = await _run(chunks, "prereq", "p1")

    cd = [f for f in frames if f["type"] == "concept_done"]
    mastered = [f for f in frames if f["type"] == "mastered"]

    # TEACHING:p1:1 backfills 개념0, CONCEPT_DONE:p1:1 마크 개념1.
    indices = sorted(f["index"] for f in cd)
    assert indices == [0, 1]
    assert all(f["chapterId"] == "p1" for f in cd)
    # C2 스키마: concept_done은 chapterId+index 키만.
    assert set(cd[0].keys()) == {"type", "chapterId", "index"}

    assert len(mastered) == 1
    assert mastered[0] == {"type": "mastered", "chapterId": "p1"}

    # 챕터가 실제로 done 처리됐고 변경 콜백이 현재 커리큘럼으로 불렸다.
    assert chapter["status"] == "done"
    assert chapter["conceptsDone"] == [True, True]
    assert changes and changes[-1] is curr


# --- (c) 자문자답 / 퇴행 루프 컷 --------------------------------------------


async def test_self_dialogue_is_cut():
    chunks = [
        "<<TEACHING:p1:0>>\n",
        "기저가 뭘까? 한번 답해봐.\n",
        "학습자: 음 잘 모르겠어요\n",          # 조작된 학습자 턴 — 여기서 컷
        "맞아, 그건 독립 벡터들의 모임이야.\n",   # 절대 새어나오면 안 됨
        "<<CONCEPT_DONE:p1:0>>",
    ]
    frames, _curr, chapter, _changes, _client = await _run(chunks, "prereq", "p1")
    text = _tokens(frames)
    assert "기저가 뭘까?" in text
    assert "학습자:" not in text
    assert "독립 벡터들의 모임" not in text
    # 컷 이후의 CONCEPT_DONE은 적용되지 않는다.
    assert chapter["conceptsDone"][0] is False


async def test_self_dialogue_labelless_is_cut():
    # 실제 버그 재현: 모델이 "직접 구해봐"로 학습자에게 차례를 넘긴 뒤, 화자
    # 레이블 없이 곧장 정답·풀이를 이어 쓰고 스스로 채점한다. 레이블이 없어
    # 기존 _SPEAKER_LABEL_RE로는 못 잡던 케이스다.
    chunks = [
        "<<TEACHING:p1:0>>\n",
        "특성방정식을 세우고 고유값을 직접 구해봐.\n",
        "\n",
        "맞아, characteristic polynomial은 (4−λ)(3−λ)−2·1 = λ²−7λ+10 "
        "= (λ−2)(λ−5), 그래서 λ=2,5.\n",   # 절대 새어나오면 안 됨
        "<<CONCEPT_DONE:p1:0>>",
    ]
    frames, _curr, chapter, _changes, _client = await _run(chunks, "prereq", "p1")
    text = _tokens(frames)
    assert "직접 구해봐" in text
    assert "characteristic polynomial" not in text
    assert "λ=2,5" not in text
    assert chapter["conceptsDone"][0] is False


async def test_legit_tutor_turn_with_rhetorical_question_not_cut():
    # 오탐 방지: 한 턴 안에서 수사적 질문 뒤 곧바로 설명을 이어가는 정상 흐름은
    # 자르지 않는다(빈 줄 분리도, 채점 표현도 없음).
    chunks = [
        "<<TEACHING:p1:0>>\n",
        "기저가 왜 중요할까? 그건 벡터공간의 모든 원소를 유일하게 표현하기\n",
        "때문이야. 자, 그럼 $\\mathbb{R}^2$의 기저를 하나 말해볼래?",
    ]
    frames, _curr, chapter, _changes, _client = await _run(chunks, "prereq", "p1")
    text = _tokens(frames)
    assert "유일하게 표현" in text
    assert "기저를 하나 말해볼래?" in text


async def test_stop_sequences_forwarded_to_client():
    chunks = ["<<TEACHING:p1:0>>\n", "기저를 설명할게."]
    _frames, _curr, _ch, _changes, client = await _run(chunks, "prereq", "p1")
    assert client.last_stop, "stop 시퀀스가 client.stream으로 전달돼야 한다"
    assert any("학습자" in s for s in client.last_stop)


async def test_degenerate_loop_is_cut():
    repeat = "다음으로 ㄱㄱ\n"
    chunks = ["<<TEACHING:p1:0>>\n", "기저를 설명할게.\n"] + [repeat] * 8
    frames, _curr, _ch, _changes, _client = await _run(chunks, "prereq", "p1")
    text = _tokens(frames)
    assert "기저를 설명할게." in text
    # 루프는 limit 횟수 이후 잘린다(무한히 반복되지 않음).
    assert text.count("다음으로 ㄱㄱ") < 8


# --- (d) 트랙별 build_messages 컨텍스트 -------------------------------------


def test_build_messages_prereq_has_no_material_and_prereq_system():
    curr = _curriculum()
    track = next(t for t in curr["tracks"] if t["id"] == "prereq")
    chapter = track["chapters"][0]
    msgs = tutor.build_messages(FakeSession(), curr, track, chapter, [])
    assert msgs[0]["content"] == TUTOR_SYSTEM_PREREQ
    # user 컨텍스트엔 개요 텍스트 블록만(자료/소스 블록 없음).
    user_blocks = msgs[1]["content"]
    assert all(b["type"] == "text" for b in user_blocks)
    joined = "\n".join(b["text"] for b in user_blocks)
    assert "벡터공간" in joined
    assert "참고 소스" not in joined


def test_build_messages_landscape_injects_source_block():
    curr = _curriculum()
    track = next(t for t in curr["tracks"] if t["id"] == "landscape")
    chapter = track["chapters"][0]
    msgs = tutor.build_messages(FakeSession(), curr, track, chapter, [])
    assert msgs[0]["content"] == TUTOR_SYSTEM_LANDSCAPE
    joined = "\n".join(b["text"] for b in msgs[1]["content"])
    assert "참고 소스" in joined
    assert "Sequence to Sequence Learning" in joined   # sourceId lookup 성공
    assert "Sutskever" in joined
    assert "10.0/seq2seq" in joined                     # 출처 링크


def test_build_messages_trends_uses_trends_system_and_source():
    curr = _curriculum()
    track = next(t for t in curr["tracks"] if t["id"] == "trends")
    chapter = track["chapters"][0]
    msgs = tutor.build_messages(FakeSession(), curr, track, chapter, [])
    assert msgs[0]["content"] == TUTOR_SYSTEM_TRENDS
    joined = "\n".join(b["text"] for b in msgs[1]["content"])
    assert "BERT" in joined


def test_build_messages_paper_uses_paper_system():
    curr = _curriculum()
    track = next(t for t in curr["tracks"] if t["id"] == "paper")
    chapter = track["chapters"][0]
    # 비-pdf 세션이라 논문 페이지 블록은 추가되지 않지만 시스템/개요는 paper용.
    msgs = tutor.build_messages(FakeSession(kind="md"), curr, track, chapter, [])
    assert msgs[0]["content"] == TUTOR_SYSTEM_PAPER
    joined = "\n".join(
        b["text"] for b in msgs[1]["content"] if b["type"] == "text"
    )
    assert "self-attention" in joined


def test_build_messages_includes_prior_thread():
    curr = _curriculum()
    track = next(t for t in curr["tracks"] if t["id"] == "prereq")
    chapter = track["chapters"][0]
    thread = [
        {"role": "user", "content": "안녕"},
        {"role": "assistant", "content": "<<TEACHING:p1:0>> 시작하자"},
        {"role": "system", "content": "무시될 것"},  # user/assistant만 통과
    ]
    msgs = tutor.build_messages(FakeSession(), curr, track, chapter, thread)
    roles = [m["role"] for m in msgs]
    # system, user(context), assistant(ack), user(안녕), assistant(시작하자)
    assert roles == ["system", "user", "assistant", "user", "assistant"]
    assert msgs[-1]["content"] == "<<TEACHING:p1:0>> 시작하자"
