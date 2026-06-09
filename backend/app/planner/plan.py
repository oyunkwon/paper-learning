"""Planner orchestrator: paper -> 4-track curriculum.

Ties the stages together and reports progress so the upload endpoint can show a
real bar (matching `learning`'s background-planning pattern):

  stage 0 ingest      (cheap, no LLM)
  stage 1 comprehend  1 LLM pass (or N image batches)
  stage 2 acquire     external retrieval (cached HTTP)
  stage 3 assemble    flatten sources (cheap)
  stage 4 project     4 LLM track-builders

Progress is reported as coarse stage steps (total = 6): identify, comprehend,
acquire, paper-track, prereq-track, landscape+trends. Good enough for a bar
without threading fine-grained counts through every stage.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Awaitable, Callable

from app.llm import LLMClient
from app.planner.acquire import acquire, to_sources
from app.planner.comprehend import comprehend
from app.planner.ingest import ingest_pdf
from app.planner.project import project
from app.retrieval.arxiv import Arxiv
from app.retrieval.http import CachedHTTP
from app.retrieval.identify import identify
from app.retrieval.openalex import OpenAlex
from app.retrieval.semantic_scholar import SemanticScholar
from app.schema import CurriculumError

log = logging.getLogger("paper.planner")

ProgressCb = Callable[[int, int], Awaitable[None]]

_TOTAL_STEPS = 6


async def plan_paper(
    *,
    kind: str,
    material_path: Path,
    pages_dir: Path | None,
    client: LLMClient,
    model: str,
    http: CachedHTTP,
    on_progress: ProgressCb | None = None,
) -> dict[str, Any]:
    """Run the full planner pipeline and return a normalized curriculum.

    ``http`` is the shared cached client for the retrieval layer (caller owns its
    lifecycle). Raises CurriculumError if the paper can't be turned into any
    usable track."""
    s2, openalex, arxiv = SemanticScholar(http), OpenAlex(http), Arxiv(http)
    done = 0

    async def step() -> None:
        nonlocal done
        done += 1
        if on_progress:
            await on_progress(done, _TOTAL_STEPS)

    if on_progress:
        await on_progress(0, _TOTAL_STEPS)

    # Stage 0 + identify: ids from the document, then cross-API identity.
    arxiv_id = doi = None
    if kind == "pdf":
        ing = ingest_pdf(material_path)
        arxiv_id, doi = ing.arxiv_id, ing.doi
    await step()

    # Stage 1: comprehend (paper-only). Also yields a reliable title.
    comprehension = await comprehend(
        client=client, model=model, kind=kind,
        material_path=material_path, pages_dir=pages_dir,
    )
    await step()

    # Identity: prefer document ids; fall back to the comprehended title.
    identity = await identify(
        s2, openalex, arxiv_id=arxiv_id, doi=doi, title=comprehension.title
    )
    if identity is None:
        # No external grounding available — we can still teach prereq + paper.
        from app.retrieval.types import PaperIdentity
        identity = PaperIdentity(title=comprehension.title, arxiv_id=arxiv_id, doi=doi)
        log.info("plan: no external identity; prereq+paper tracks only")

    # Stage 2: acquire grounded context.
    acquired = await acquire(identity, s2=s2, openalex=openalex, arxiv=arxiv)
    await step()

    # Stage 3: assemble sources[].
    sources = to_sources(acquired)

    # Stage 4: project into tracks (project reports its own 3 sub-steps).
    curriculum = await _project_with_progress(
        client, model, identity, comprehension, acquired, sources, step
    )

    if not curriculum.get("tracks"):
        raise CurriculumError("어떤 트랙도 생성하지 못했습니다.")
    log.info(
        "plan done: %d tracks for %r",
        len(curriculum["tracks"]), identity.title,
    )
    return curriculum


async def _project_with_progress(
    client, model, identity, comprehension, acquired, sources, step
) -> dict[str, Any]:
    """Run projection, ticking progress around it. project() builds 4 tracks in
    one call; we tick before/after to fill the remaining 3 steps coarsely."""
    curriculum = await project(
        client=client, model=model, identity=identity,
        comprehension=comprehension, acquired=acquired, sources=sources,
    )
    # paper, prereq, landscape+trends -> 3 ticks.
    await step()
    await step()
    await step()
    return curriculum
