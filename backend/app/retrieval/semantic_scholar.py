"""Semantic Scholar client.

Role (validated, DESIGN.md): paper identification + references with the
``isInfluential`` flag. This is the landscape backbone — influential references
are the load-bearing prior work the paper builds on.

NOT used for the trends view: the /citations endpoint returns newest-first and
won't sort by impact, and a landmark paper can have >100k citations. Trends use
OpenAlex's server-side cited_by_count sort instead.
"""

from __future__ import annotations

import logging
from typing import Any

from app.retrieval.http import CachedHTTP, RetrievalError
from app.retrieval.types import PaperRef

log = logging.getLogger("paper.retrieval.s2")

_BASE = "https://api.semanticscholar.org/graph/v1"

# Fields for a paper lookup: identity + counts + externalIds (for DOI -> OpenAlex).
_PAPER_FIELDS = "title,year,abstract,venue,authors,citationCount,referenceCount,influentialCitationCount,externalIds"
# Fields for reference rows: enough to ground + rank a landscape entry.
# isInfluential is an edge field (per reference), the landscape backbone signal.
_REF_FIELDS = "isInfluential,title,year,authors,venue,citationCount,externalIds,abstract"


class SemanticScholar:
    def __init__(self, http: CachedHTTP) -> None:
        self._http = http

    async def get_paper(self, *, arxiv_id: str | None = None, doi: str | None = None,
                        paper_id: str | None = None) -> dict[str, Any] | None:
        """Fetch a paper by arXiv id / DOI / S2 paperId. Returns the raw S2
        object (caller maps to PaperIdentity) or None if not found."""
        ident = self._paper_path(arxiv_id=arxiv_id, doi=doi, paper_id=paper_id)
        if ident is None:
            return None
        try:
            obj = await self._http.get_json(
                f"{_BASE}/paper/{ident}", params={"fields": _PAPER_FIELDS}
            )
        except RetrievalError as e:
            log.info("S2 get_paper(%s) failed: %s", ident, e)
            return None
        return obj if isinstance(obj, dict) else None

    async def references(self, paper_id: str, *, limit: int = 100) -> list[PaperRef]:
        """The paper's references, mapped to PaperRef with is_influential set.

        These feed the landscape view. Returns at most ``limit`` (S2 caps a page
        at 1000; references rarely exceed a few hundred)."""
        try:
            data = await self._http.get_json(
                f"{_BASE}/paper/{paper_id}/references",
                params={"fields": _REF_FIELDS, "limit": min(limit, 1000)},
            )
        except RetrievalError as e:
            log.info("S2 references(%s) failed: %s", paper_id, e)
            return []
        rows = data.get("data", []) if isinstance(data, dict) else []
        refs: list[PaperRef] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            cited = row.get("citedPaper")
            if not isinstance(cited, dict):
                continue
            ref = _to_ref(cited, is_influential=bool(row.get("isInfluential")))
            if ref is not None and ref.has_min_metadata:
                refs.append(ref)
        return refs

    # ----- internals ---------------------------------------------------
    @staticmethod
    def _paper_path(*, arxiv_id: str | None, doi: str | None,
                    paper_id: str | None) -> str | None:
        if arxiv_id:
            return f"arXiv:{arxiv_id}"
        if doi:
            return f"DOI:{doi}"
        if paper_id:
            return paper_id
        return None


def _to_ref(obj: dict[str, Any], *, is_influential: bool) -> PaperRef | None:
    title = obj.get("title")
    if not isinstance(title, str) or not title.strip():
        return None
    ext = obj.get("externalIds") or {}
    authors = tuple(
        a.get("name", "") for a in (obj.get("authors") or []) if isinstance(a, dict)
    )
    return PaperRef(
        title=title.strip(),
        retrieved_from="semantic_scholar",
        paper_id=obj.get("paperId"),
        arxiv_id=ext.get("ArXiv") if isinstance(ext, dict) else None,
        doi=ext.get("DOI") if isinstance(ext, dict) else None,
        year=_opt_int(obj.get("year")),
        authors=tuple(a for a in authors if a),
        venue=obj.get("venue") or None,
        abstract=obj.get("abstract") or None,
        citation_count=_opt_int(obj.get("citationCount")),
        is_influential=is_influential,
    )


def _opt_int(v: Any) -> int | None:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None
