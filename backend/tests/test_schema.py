"""Unit tests for the multi-track schema + runtime mutations (no network)."""

from __future__ import annotations

import pytest

from app import runtime as rt
from app.schema import CurriculumError, normalize_curriculum


def _raw():
    return {
        "paper": {"title": "테스트 논문", "year": 2020, "arxivId": "2001.00001"},
        "tracks": [
            {
                "id": "prereq",
                "groups": [
                    {"id": "g_la", "title": "선형대수", "textbook": "Strang",
                     "chapterIds": ["p1", "ghost"]},
                ],
                "chapters": [
                    {"id": "p1", "title": "벡터공간", "concepts": ["기저", "차원"]},
                    {"id": "p2", "title": "고유값", "concepts": ["고유벡터"]},
                ],
            },
            {
                "id": "paper",
                "chapters": [
                    {"id": "c1", "title": "주장", "concepts": ["핵심주장"],
                     "pageStart": 1, "pageEnd": 3},
                ],
            },
        ],
        "sources": [
            {"id": "s1", "title": "Strang LA", "type": "textbook",
             "retrievedFrom": "web", "url": "http://x"},
            {"title": "ungrounded"},                 # dropped: no retrievedFrom
            {"id": "s1", "title": "dup", "retrievedFrom": "web"},  # dropped: dup id
        ],
    }


def test_normalize_attaches_runtime_and_orders_tracks():
    c = normalize_curriculum(_raw())
    # paper track sorts before prereq per TRACK_ORDER.
    assert [t["id"] for t in c["tracks"]] == ["paper", "prereq"]
    prereq = rt.find_track(c, "prereq")
    p1 = prereq["chapters"][0]
    assert p1["status"] == "active"          # first chapter active
    assert p1["conceptsDone"] == [False, False]
    assert p1["known"] is False              # prereq-only field
    assert prereq["chapters"][1]["status"] == "locked"
    assert prereq["kind"] == "dependency"


def test_groups_drop_unknown_chapter_ids():
    c = normalize_curriculum(_raw())
    groups = rt.find_track(c, "prereq")["groups"]
    assert groups[0]["chapterIds"] == ["p1"]   # "ghost" removed


def test_sources_require_provenance_and_dedup():
    c = normalize_curriculum(_raw())
    assert [s["id"] for s in c["sources"]] == ["s1"]   # only the grounded, unique one


def test_empty_tracks_raise():
    with pytest.raises(CurriculumError):
        normalize_curriculum({"tracks": []})
    with pytest.raises(CurriculumError):
        normalize_curriculum({"tracks": [{"id": "prereq", "chapters": []}]})


def test_teaching_marker_backfills_prior_concepts():
    c = normalize_curriculum(_raw())
    p1 = rt.find_chapter(c, "prereq", "p1")
    # Reaching concept index 1 means concept 0 was taught + passed.
    newly = rt.mark_concepts_before(p1, 1)
    assert newly == [0]
    assert p1["conceptsDone"] == [True, False]
    assert p1["status"] == "active"          # not all done yet


def test_concept_done_completes_chapter():
    c = normalize_curriculum(_raw())
    p2 = rt.find_chapter(c, "prereq", "p2")
    assert rt.mark_concept_done(p2, 0)
    assert p2["status"] == "done"            # its only concept done -> chapter done


def test_known_toggle_marks_done_and_skips_active():
    c = normalize_curriculum(_raw())
    prereq = rt.find_track(c, "prereq")
    p1 = prereq["chapters"][0]
    rt.set_known(p1, True)
    assert p1["status"] == "done"
    assert p1["known"] is True
    # active_chapter now skips the known p1 and lands on p2.
    assert rt.active_chapter(c, "prereq")["id"] == "p2"


def test_pass_current_concept():
    c = normalize_curriculum(_raw())
    p1 = rt.find_chapter(c, "prereq", "p1")
    idx, done = rt.pass_current_concept(p1)
    assert idx == 0 and done is False
    assert p1["conceptsDone"] == [True, False]


def test_progress_counts_known_as_done():
    c = normalize_curriculum(_raw())
    prereq = rt.find_track(c, "prereq")
    rt.set_known(prereq["chapters"][0], True)
    assert rt.track_progress(prereq) == {"done": 1, "total": 2}
    assert rt.progress(c)["prereq"]["done"] == 1
