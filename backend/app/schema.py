"""Multi-track curriculum: schema, normalization, and runtime mutations.

A paper-learning curriculum is the projection of the paper's grounded knowledge
graph into four tracks (DESIGN.md D1/D5):

    prereq     dependency-ordered foundation map (course groups -> topics)
    landscape  the problem-area map (references + surveys), reading mode
    trends     forward impact view (citing papers + recent surveys), reading mode
    paper      the paper itself: claims / results / limits / insights

Each track holds learning-style chapters (concepts[], conceptsDone[], status),
so the tutoring loop ported from `learning` works per chapter regardless of
track. The prereq track additionally carries course `groups` and per-chapter
`known` (the learner can toggle off what they already know — D3).

Persisted shape (sessions.curriculum JSONB):
{
  "paper":   { "title", "authors", "arxivId", "doi", "year", "abstract" },
  "tracks":  [ { "id", "kind", "title", "summary", "groups"?, "chapters":[...] } ],
  "graph":   { "nodes":[...], "edges":[...] },   # provenance, optional
  "sources": [ { "id", "type", "title", "url", "doi", "retrievedFrom", ... } ]
}

Chapter shape (mirrors learning + paper-learning extensions):
{
  "id", "title", "summary",
  "concepts": [...], "conceptsDone": [bool...], "status": "active|locked|done",
  "pageStart": int|null, "pageEnd": int|null,    # paper track only
  "sourceIds": [...],                            # grounding provenance
  "known": bool                                  # prereq track: learner toggle
}
"""

from __future__ import annotations

from typing import Any, Literal

TrackId = Literal["prereq", "landscape", "trends", "paper"]
TrackKind = Literal["dependency", "reading"]

TRACK_ORDER: tuple[str, ...] = ("paper", "prereq", "landscape", "trends")
TRACK_KIND: dict[str, str] = {
    "prereq": "dependency",
    "landscape": "reading",
    "trends": "reading",
    "paper": "reading",
}
TRACK_TITLE: dict[str, str] = {
    "prereq": "선수지식",
    "landscape": "전체 지형 (Landscape)",
    "trends": "트렌드 & 임팩트",
    "paper": "논문 자체",
}


class CurriculumError(RuntimeError):
    """Raised when a curriculum can't be assembled into a usable shape."""


# --- normalization ----------------------------------------------------------


def normalize_chapter(ch: dict[str, Any], index: int, *, is_prereq: bool) -> dict[str, Any]:
    """Validate one chapter and attach runtime fields. Idempotent: re-normalizing
    a chapter that already has runtime fields preserves its progress."""
    cid = ch.get("id") or f"ch{index + 1}"
    concepts = ch.get("concepts")
    concepts = [str(c) for c in concepts] if isinstance(concepts, list) else []

    done = ch.get("conceptsDone")
    if isinstance(done, list) and len(done) == len(concepts):
        concepts_done = [bool(x) for x in done]
    else:
        concepts_done = [False] * len(concepts)

    status = ch.get("status")
    if status not in ("active", "locked", "done"):
        # First chapter of a track is active; the rest start locked.
        status = "active" if index == 0 else "locked"

    out: dict[str, Any] = {
        "id": str(cid),
        "title": str(ch.get("title", f"챕터 {index + 1}")),
        "summary": str(ch.get("summary", "")),
        "concepts": concepts,
        "conceptsDone": concepts_done,
        "status": status,
        "pageStart": _opt_int(ch.get("pageStart")),
        "pageEnd": _opt_int(ch.get("pageEnd")),
        "sourceIds": [str(s) for s in ch.get("sourceIds", []) if s is not None],
    }
    if is_prereq:
        out["known"] = bool(ch.get("known", False))
    return out


def normalize_track(track: dict[str, Any]) -> dict[str, Any]:
    tid = track.get("id")
    if tid not in TRACK_ORDER:
        raise CurriculumError(f"알 수 없는 트랙: {tid}")
    is_prereq = tid == "prereq"
    chapters_in = track.get("chapters")
    if not isinstance(chapters_in, list):
        chapters_in = []
    chapters = [
        normalize_chapter(ch, i, is_prereq=is_prereq)
        for i, ch in enumerate(chapters_in)
        if isinstance(ch, dict)
    ]
    out: dict[str, Any] = {
        "id": tid,
        "kind": TRACK_KIND[tid],
        "title": str(track.get("title", TRACK_TITLE[tid])),
        "summary": str(track.get("summary", "")),
        "chapters": chapters,
    }
    if is_prereq:
        out["groups"] = _normalize_groups(track.get("groups"), chapters)
    return out


def _normalize_groups(
    groups_in: Any, chapters: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Course-unit groups for the prereq track. Drops references to unknown
    chapter ids so the UI never points at a missing chapter."""
    valid_ids = {c["id"] for c in chapters}
    out: list[dict[str, Any]] = []
    if not isinstance(groups_in, list):
        return out
    for i, g in enumerate(groups_in):
        if not isinstance(g, dict):
            continue
        chapter_ids = [
            str(c) for c in g.get("chapterIds", []) if str(c) in valid_ids
        ]
        out.append(
            {
                "id": str(g.get("id") or f"g{i + 1}"),
                "title": str(g.get("title", f"과목 {i + 1}")),
                "textbook": str(g["textbook"]) if g.get("textbook") else None,
                "chapterIds": chapter_ids,
            }
        )
    return out


def normalize_curriculum(parsed: dict[str, Any]) -> dict[str, Any]:
    """Validate the whole curriculum and attach runtime fields. At least one
    track with at least one chapter is required."""
    if not isinstance(parsed, dict):
        raise CurriculumError("커리큘럼이 객체가 아닙니다.")

    tracks_in = parsed.get("tracks")
    if not isinstance(tracks_in, list) or not tracks_in:
        raise CurriculumError("커리큘럼에 트랙이 없습니다.")

    tracks = [normalize_track(t) for t in tracks_in if isinstance(t, dict)]
    tracks = [t for t in tracks if t["chapters"]]
    if not tracks:
        raise CurriculumError("유효한 챕터가 있는 트랙이 없습니다.")

    # Stable, meaningful order regardless of how the planner emitted them.
    tracks.sort(key=lambda t: TRACK_ORDER.index(t["id"]))

    return {
        "paper": _normalize_paper(parsed.get("paper")),
        "tracks": tracks,
        "graph": parsed.get("graph") if isinstance(parsed.get("graph"), dict) else {"nodes": [], "edges": []},
        "sources": _normalize_sources(parsed.get("sources")),
    }


def _normalize_paper(paper: Any) -> dict[str, Any]:
    if not isinstance(paper, dict):
        return {"title": "논문"}
    return {
        "title": str(paper.get("title", "논문")),
        "authors": [str(a) for a in paper.get("authors", []) if a is not None],
        "arxivId": paper.get("arxivId") or None,
        "doi": paper.get("doi") or None,
        "year": _opt_int(paper.get("year")),
        "abstract": paper.get("abstract") or None,
        "venue": paper.get("venue") or None,
    }


def _normalize_sources(sources: Any) -> list[dict[str, Any]]:
    """Sources carry provenance (D2). A source with no title or no provenance is
    dropped — we never show an ungrounded citation."""
    out: list[dict[str, Any]] = []
    if not isinstance(sources, list):
        return out
    seen: set[str] = set()
    for s in sources:
        if not isinstance(s, dict):
            continue
        title = s.get("title")
        retrieved = s.get("retrievedFrom")
        if not title or not retrieved:
            continue
        sid = str(s.get("id") or s.get("doi") or s.get("url") or title)
        if sid in seen:
            continue
        seen.add(sid)
        out.append(
            {
                "id": sid,
                "type": s.get("type") or "paper",
                "title": str(title),
                "authors": [str(a) for a in s.get("authors", []) if a is not None],
                "url": s.get("url") or None,
                "doi": s.get("doi") or None,
                "arxivId": s.get("arxivId") or None,
                "year": _opt_int(s.get("year")),
                "venue": s.get("venue") or None,
                "retrievedFrom": str(retrieved),
            }
        )
    return out


def _opt_int(v: Any) -> int | None:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None
