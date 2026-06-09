"""Stage 4: Project — emit the 4-track curriculum from comprehension + sources.

Four independent track builders (each a small LLM call), assembled into the
multi-track curriculum and normalized:

  prereq      from presupposed concepts (course-unit foundation map, D3)
  paper       from the comprehension (claims/results/limits/insights)
  landscape   from references + surveys (grounded, D2)
  trends      from citing papers + surveys (grounded, D2)

Landscape/trends builders only ever see the retrieved source list and must cite
source ids — they cannot introduce papers that weren't retrieved.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from app.jsonparse import JSONParseError, parse_json_object
from app.llm import LLMClient
from app.planner.acquire import Acquired
from app.planner.comprehend import Comprehension
from app.planner.prompts import (
    LANDSCAPE_SYSTEM,
    PAPER_TRACK_SYSTEM,
    PREREQ_SYSTEM,
    TRENDS_SYSTEM,
    paper_track_user,
    prereq_user,
)
from app.retrieval.types import PaperIdentity, PaperRef
from app.schema import normalize_curriculum

log = logging.getLogger("paper.planner.project")


@dataclass
class _SourceView:
    """A source dict enriched with the ranking hints the landscape/trends prompts
    use (influential / citationCount), keyed by the id the model must reuse."""

    src: dict[str, Any]
    influential: bool | None = None
    citation_count: int | None = None


async def project(
    *,
    client: LLMClient,
    model: str,
    identity: PaperIdentity,
    comprehension: Comprehension,
    acquired: Acquired,
    sources: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build all four tracks and assemble the normalized curriculum."""
    tracks: list[dict[str, Any]] = []

    paper_track = await _paper_track(client, model, comprehension)
    if paper_track:
        tracks.append(paper_track)

    prereq_track = await _prereq_track(client, model, comprehension, identity.title)
    if prereq_track:
        tracks.append(prereq_track)

    landscape_track = await _grounded_track(
        client, model, "landscape", LANDSCAPE_SYSTEM,
        "이 논문이 인용한 선행연구 + survey다. 지형 트랙을 설계해줘.",
        _views(acquired.references, acquired.surveys, sources),
    )
    if landscape_track:
        tracks.append(landscape_track)

    trends_track = await _grounded_track(
        client, model, "trends", TRENDS_SYSTEM,
        "이 논문을 인용한 후속 연구 + survey다. 트렌드/임팩트 트랙을 설계해줘.",
        _views(acquired.citing, acquired.surveys, sources),
    )
    if trends_track:
        tracks.append(trends_track)

    curriculum = {
        "paper": {
            "title": identity.title,
            "authors": list(identity.authors),
            "arxivId": identity.arxiv_id,
            "doi": identity.doi,
            "year": identity.year,
            "abstract": identity.abstract,
            "venue": identity.venue,
        },
        "tracks": tracks,
        "sources": sources,
        "graph": {"nodes": [], "edges": []},
    }
    return normalize_curriculum(curriculum)


async def _paper_track(
    client: LLMClient, model: str, c: Comprehension
) -> dict[str, Any] | None:
    payload = json.dumps(
        {
            "title": c.title,
            "motivation": c.motivation,
            "claims": c.claims,
            "method": c.method,
            "results": c.results,
            "limitations": c.limitations,
            "insights": c.insights,
            "introduced": c.introduced,
        },
        ensure_ascii=False,
    )
    obj = await _run(client, model, PAPER_TRACK_SYSTEM, paper_track_user(payload))
    if obj is None:
        return None
    return {"id": "paper", "chapters": obj.get("chapters", [])}


async def _prereq_track(
    client: LLMClient, model: str, c: Comprehension, paper_title: str
) -> dict[str, Any] | None:
    if not c.presupposed:
        log.info("no presupposed concepts; skipping prereq track")
        return None
    obj = await _run(
        client, model, PREREQ_SYSTEM, prereq_user(c.presupposed, paper_title)
    )
    if obj is None:
        return None
    return {
        "id": "prereq",
        "groups": obj.get("groups", []),
        "chapters": obj.get("chapters", []),
    }


async def _grounded_track(
    client: LLMClient,
    model: str,
    track_id: str,
    system: str,
    intro: str,
    views: list[_SourceView],
) -> dict[str, Any] | None:
    if not views:
        log.info("no sources for %s track; skipping", track_id)
        return None
    payload = [
        {
            "id": v.src["id"],
            "title": v.src["title"],
            "year": v.src.get("year"),
            "venue": v.src.get("venue"),
            "influential": v.influential,
            "citationCount": v.citation_count,
        }
        for v in views
    ]
    user = intro + "\n\n실제 소스 목록(JSON):\n" + json.dumps(payload, ensure_ascii=False)
    obj = await _run(client, model, system, user)
    if obj is None:
        return None
    return {"id": track_id, "chapters": obj.get("chapters", [])}


def _views(
    refs: list[PaperRef], surveys: list[PaperRef], sources: list[dict[str, Any]]
) -> list[_SourceView]:
    """Build ranking-enriched views for the source ids present in this bucket.
    Maps each ref to its source dict (by the same dedup key used in to_sources)."""
    by_key = {s["id"]: s for s in sources}
    views: list[_SourceView] = []
    seen: set[str] = set()
    for r in [*refs, *surveys]:
        key = (r.doi or r.arxiv_id or r.title).lower()
        if key in seen or key not in by_key:
            continue
        seen.add(key)
        views.append(
            _SourceView(
                src=by_key[key],
                influential=r.is_influential,
                citation_count=r.citation_count,
            )
        )
    return views


async def _run(
    client: LLMClient, model: str, system: str, user: str
) -> dict[str, Any] | None:
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    try:
        raw = await client.complete(model=model, messages=messages, temperature=0.2)
        return parse_json_object(raw)
    except JSONParseError as e:
        log.warning("track build unparsable: %s", e)
        return None
