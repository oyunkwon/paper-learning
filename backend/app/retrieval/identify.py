"""Resolve an uploaded paper to a stable cross-API identity.

The validated resolution flow (DESIGN.md):

    arXiv id  --S2-->  paper (title, DOI from externalIds, counts)
              --OpenAlex(DOI or title)-->  canonical work id  -->  topics

Why this order: Semantic Scholar identifies instantly from an arXiv id and hands
us the DOI. OpenAlex's canonical id must be resolved via DOI/title (never the
MAG id), so we do that second, then pull topics for area framing.

When there's no arXiv id (a non-arXiv PDF), the caller passes a title/DOI parsed
from the document and we resolve from whichever is available.
"""

from __future__ import annotations

import logging

from app.retrieval.openalex import OpenAlex
from app.retrieval.semantic_scholar import SemanticScholar
from app.retrieval.types import PaperIdentity

log = logging.getLogger("paper.retrieval.identify")


async def identify(
    s2: SemanticScholar,
    openalex: OpenAlex,
    *,
    arxiv_id: str | None = None,
    doi: str | None = None,
    title: str | None = None,
) -> PaperIdentity | None:
    """Resolve identity from any starting point (arXiv id / DOI / title).

    Returns a PaperIdentity with as many cross-API ids as could be resolved, or
    None if nothing matched (the pipeline then falls back to a document-only
    plan with no external grounding)."""
    s2_obj = None
    if arxiv_id or doi:
        s2_obj = await s2.get_paper(arxiv_id=arxiv_id, doi=doi)

    # Seed identity from S2 when available; else from what we were given.
    if s2_obj:
        ext = s2_obj.get("externalIds") or {}
        ident = PaperIdentity(
            title=s2_obj.get("title") or title or "(제목 미상)",
            arxiv_id=(ext.get("ArXiv") if isinstance(ext, dict) else None) or arxiv_id,
            doi=(ext.get("DOI") if isinstance(ext, dict) else None) or doi,
            s2_paper_id=s2_obj.get("paperId"),
            year=_opt_int(s2_obj.get("year")),
            authors=tuple(
                a.get("name", "")
                for a in (s2_obj.get("authors") or [])
                if isinstance(a, dict) and a.get("name")
            ),
            abstract=s2_obj.get("abstract") or None,
            venue=s2_obj.get("venue") or None,
            citation_count=_opt_int(s2_obj.get("citationCount")),
        )
    elif title or doi or arxiv_id:
        ident = PaperIdentity(title=title or "(제목 미상)", arxiv_id=arxiv_id, doi=doi)
    else:
        return None

    # Resolve OpenAlex canonical id from DOI/title, then attach topics.
    oa_id = await openalex.resolve_id(doi=ident.doi, title=ident.title)
    if oa_id:
        ident.openalex_id = oa_id
        ident.topics = await openalex.topics(oa_id)

    if not ident.resolved:
        log.info("identify: no cross-API id resolved for %r", ident.title)
        return None
    return ident


def _opt_int(v: object) -> int | None:
    try:
        return int(v) if v is not None else None  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
