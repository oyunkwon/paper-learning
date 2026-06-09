"""Runtime curriculum mutations driven by the tutor's progress signals.

Ported from `learning`'s curriculum.py, made track-aware: a chapter is now
addressed by (track_id, chapter_id) instead of a single chapter id, since the
same chapter id could repeat across tracks. The progress-signal semantics are
identical to `learning` so the ported tutor loop works unchanged:

  <<TEACHING:cid:N>>      reaching concept N implies 0..N-1 are done
  <<CONCEPT_DONE:cid:N>>  concept N passed
  <<MASTERED:cid>>        chapter complete

The tutor's sentinels only carry a chapter id (not a track id), so the caller
resolves the track from context (the active chat is always within one track) and
passes it here.
"""

from __future__ import annotations

from typing import Any


# --- lookup -----------------------------------------------------------------


def find_track(curriculum: dict[str, Any], track_id: str) -> dict[str, Any] | None:
    for t in curriculum.get("tracks", []):
        if t.get("id") == track_id:
            return t
    return None


def find_chapter(
    curriculum: dict[str, Any], track_id: str, chapter_id: str
) -> dict[str, Any] | None:
    track = find_track(curriculum, track_id)
    if track is None:
        return None
    for ch in track.get("chapters", []):
        if ch.get("id") == chapter_id:
            return ch
    return None


def active_chapter(
    curriculum: dict[str, Any], track_id: str
) -> dict[str, Any] | None:
    """The track's active chapter, or its first non-done chapter, or None.

    Chapters the learner marked `known` (prereq track) are skipped — they're
    treated as already-satisfied, so teaching lands on the first unknown one."""
    track = find_track(curriculum, track_id)
    if track is None:
        return None
    chapters = track.get("chapters", [])
    for ch in chapters:
        if ch.get("status") == "active" and not ch.get("known"):
            return ch
    for ch in chapters:
        if ch.get("status") != "done" and not ch.get("known"):
            return ch
    return None


# --- progress-signal mutations (chapter-scoped, same semantics as learning) --


def mark_concept_done(ch: dict[str, Any], concept_index: int) -> bool:
    done = ch.get("conceptsDone") or []
    if 0 <= concept_index < len(done):
        done[concept_index] = True
        ch["conceptsDone"] = done
        _sync_chapter_status(ch)
        return True
    return False


def mark_concepts_before(ch: dict[str, Any], concept_index: int) -> list[int]:
    """Mark every concept with index < concept_index as done. Returns the indices
    newly flipped (so the caller can emit per-concept frames)."""
    done = ch.get("conceptsDone") or []
    newly: list[int] = []
    for i in range(min(concept_index, len(done))):
        if not done[i]:
            done[i] = True
            newly.append(i)
    if newly:
        ch["conceptsDone"] = done
        _sync_chapter_status(ch)
    return newly


def mark_chapter_done(ch: dict[str, Any]) -> bool:
    ch["status"] = "done"
    ch["conceptsDone"] = [True] * len(ch.get("concepts", []))
    return True


def current_concept_index(ch: dict[str, Any]) -> int | None:
    """First not-yet-done concept; None when all are done."""
    done = ch.get("conceptsDone") or []
    n = len(ch.get("concepts", []))
    for i in range(n):
        if i >= len(done) or not done[i]:
            return i
    return None


def pass_current_concept(ch: dict[str, Any]) -> tuple[int | None, bool]:
    """Skip the chapter's current concept (mark done). Returns
    (passed_index, chapter_done). passed_index is None if nothing was left."""
    idx = current_concept_index(ch)
    if idx is None:
        return None, ch.get("status") == "done"
    concepts = ch.get("concepts", [])
    done = ch.get("conceptsDone") or []
    while len(done) < len(concepts):
        done.append(False)
    done[idx] = True
    ch["conceptsDone"] = done
    _sync_chapter_status(ch)
    return idx, ch.get("status") == "done"


def _sync_chapter_status(ch: dict[str, Any]) -> None:
    done = ch.get("conceptsDone") or []
    if done and all(done):
        ch["status"] = "done"
    elif ch.get("status") == "done":
        ch["status"] = "active"


# --- navigation (manual next / revisit / known toggle) ----------------------


def activate_chapter(ch: dict[str, Any]) -> bool:
    if ch.get("status") != "done":
        ch["status"] = "active"
    return True


def next_chapter_id(track: dict[str, Any], chapter_id: str) -> str | None:
    chapters = track.get("chapters", [])
    idx = next(
        (i for i, c in enumerate(chapters) if c.get("id") == chapter_id), None
    )
    if idx is None or idx + 1 >= len(chapters):
        return None
    cid = chapters[idx + 1].get("id")
    return cid if isinstance(cid, str) else None


def set_known(ch: dict[str, Any], known: bool) -> None:
    """Learner toggles a prereq chapter as already-known (D3). A known chapter is
    treated as done for progress and skipped by active_chapter."""
    ch["known"] = known
    if known:
        ch["status"] = "done"
        ch["conceptsDone"] = [True] * len(ch.get("concepts", []))


# --- progress summary -------------------------------------------------------


def track_progress(track: dict[str, Any]) -> dict[str, int]:
    chapters = track.get("chapters", [])
    # Known chapters count as done (the learner opted out of them).
    done = sum(
        1 for c in chapters if c.get("status") == "done" or c.get("known")
    )
    return {"done": done, "total": len(chapters)}


def progress(curriculum: dict[str, Any]) -> dict[str, dict[str, int]]:
    """Per-track progress, keyed by track id."""
    return {
        t["id"]: track_progress(t) for t in curriculum.get("tracks", [])
    }
