"""Stage 2 + 3: Acquire and Assemble grounded external context.

Acquire (D2): using the resolved PaperIdentity, retrieve
  - landscape backbone: the paper's references (Semantic Scholar), influential
    ones first;
  - trends backbone: papers citing it (OpenAlex), impact-sorted, recent;
  - optional survey enrichment: arXiv search on the paper's topic.

Assemble: dedup + bound these into a single sources[] list (each carrying
provenance) plus the buckets the projection stage consumes. Every external paper
shown downstream originates here — the projection LLM only summarizes these, it
never invents citations.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.retrieval.arxiv import Arxiv
from app.retrieval.openalex import OpenAlex
from app.retrieval.semantic_scholar import SemanticScholar
from app.retrieval.types import PaperIdentity, PaperRef

log = logging.getLogger("paper.planner.acquire")

# Bounds so the projection prompt stays small and focused on the strongest
# signals. Landscape: prefer influential references; Trends: top-cited citing.
_MAX_LANDSCAPE = 24
_MAX_TRENDS = 20
_MAX_SURVEYS = 6
# Citing papers older than this rarely represent "recent trends".
_TRENDS_YEAR_FROM = 2018


@dataclass
class Acquired:
    """Grounded buckets for the projection stage. Each PaperRef carries
    provenance (retrieved_from + url/doi)."""

    references: list[PaperRef] = field(default_factory=list)   # landscape
    citing: list[PaperRef] = field(default_factory=list)       # trends
    surveys: list[PaperRef] = field(default_factory=list)      # enrichment

    @property
    def is_empty(self) -> bool:
        return not (self.references or self.citing or self.surveys)


async def acquire(
    identity: PaperIdentity,
    *,
    s2: SemanticScholar,
    openalex: OpenAlex,
    arxiv: Arxiv,
) -> Acquired:
    """Retrieve and bound the grounded context for one identified paper."""
    references = await _landscape(identity, s2, openalex)
    citing = await _trends(identity, openalex)
    surveys = await _surveys(identity, arxiv)
    log.info(
        "acquire: %d refs, %d citing, %d surveys for %r",
        len(references), len(citing), len(surveys), identity.title,
    )
    return Acquired(references=references, citing=citing, surveys=surveys)


async def _landscape(identity: PaperIdentity, s2: SemanticScholar,
                     openalex: OpenAlex) -> list[PaperRef]:
    """References (backward citations) — the landscape backbone.

    OpenAlex is the primary source (no key, robust). Semantic Scholar's
    isInfluential flag is layered on as ENRICHMENT when reachable: S2 rate-limits
    hard without a key, so we never depend on it — a failed S2 call just means no
    influence flags, not an empty landscape."""
    refs: list[PaperRef] = []
    if identity.openalex_id:
        refs = await openalex.references(identity.openalex_id, limit=_MAX_LANDSCAPE)

    # Fallback to S2 references if OpenAlex had none (e.g. no openalex_id).
    if not refs and identity.s2_paper_id:
        refs = await s2.references(identity.s2_paper_id, limit=100)

    # Enrich with S2 influence flags when available (best-effort, never blocks).
    refs = await _enrich_influence(refs, identity, s2)

    # Influential first, then by citation count — strongest prior work on top.
    refs.sort(
        key=lambda r: (1 if r.is_influential else 0, r.citation_count or 0),
        reverse=True,
    )
    return refs[:_MAX_LANDSCAPE]


async def _enrich_influence(
    refs: list[PaperRef], identity: PaperIdentity, s2: SemanticScholar
) -> list[PaperRef]:
    """Layer S2's isInfluential onto OpenAlex refs by matching on doi/arxiv/title.
    Best-effort: if S2 is unreachable (rate-limited), return refs unchanged."""
    if not refs or not identity.s2_paper_id:
        return refs
    s2_refs = await s2.references(identity.s2_paper_id, limit=100)
    if not s2_refs:
        return refs
    influential_keys: set[str] = set()
    for r in s2_refs:
        if r.is_influential:
            for k in (r.doi, r.arxiv_id, r.title):
                if k:
                    influential_keys.add(k.lower())
    if not influential_keys:
        return refs
    out: list[PaperRef] = []
    for r in refs:
        keys = [k.lower() for k in (r.doi, r.arxiv_id, r.title) if k]
        if any(k in influential_keys for k in keys):
            out.append(_with_influence(r, True))
        else:
            out.append(r)
    return out


def _with_influence(r: PaperRef, value: bool) -> PaperRef:
    from dataclasses import replace
    return replace(r, is_influential=value)


async def _trends(identity: PaperIdentity, openalex: OpenAlex) -> list[PaperRef]:
    if not identity.openalex_id:
        return []
    # Already impact-sorted server-side; year-floored to keep it "recent".
    return await openalex.citing_papers(
        identity.openalex_id, year_from=_TRENDS_YEAR_FROM, limit=_MAX_TRENDS
    )


async def _surveys(identity: PaperIdentity, arxiv: Arxiv) -> list[PaperRef]:
    """Topic surveys to frame landscape/trends. Query from the paper's strongest
    topic, falling back to title keywords."""
    query = None
    if identity.topics:
        query = f"{identity.topics[0].name} survey"
    elif identity.title:
        query = f"{identity.title} survey"
    if not query:
        return []
    results = await arxiv.search(query, max_results=_MAX_SURVEYS)
    return results[:_MAX_SURVEYS]


def to_sources(acquired: Acquired) -> list[dict]:
    """Flatten acquired refs into normalized source dicts (schema sources[]).
    Dedup by doi/arxiv/title; carries provenance. Used by Assemble."""
    seen: set[str] = set()
    out: list[dict] = []
    for bucket, stype in (
        (acquired.references, "paper"),
        (acquired.citing, "paper"),
        (acquired.surveys, "survey"),
    ):
        for r in bucket:
            key = (r.doi or r.arxiv_id or r.title).lower()
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "id": key,
                "type": stype,
                "title": r.title,
                "authors": list(r.authors),
                "url": r.url,
                "doi": r.doi,
                "arxivId": r.arxiv_id,
                "year": r.year,
                "venue": r.venue,
                "retrievedFrom": r.retrieved_from,
            })
    return out
