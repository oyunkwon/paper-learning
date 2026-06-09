"""Shared dataclasses for the retrieval layer.

These are the *grounded* shapes: every field comes from a real API response, so
the rest of the pipeline (grounding, projection) can trust them and carry the
provenance (``retrieved_from`` + url/doi) all the way to the UI.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

# Which API a record came from. Carried as provenance to the UI (D2).
Source = Literal["semantic_scholar", "openalex", "arxiv", "web"]


@dataclass(frozen=True)
class PaperRef:
    """A single paper as returned by a citation/search API. The atomic unit of
    the landscape (references) and trends (citing papers) views.

    Only ``title`` is required-ish in practice; metadata is noisy (see DESIGN.md
    trap #3), so callers drop records with no title and sanity-check the rest.
    """

    title: str
    retrieved_from: Source
    paper_id: str | None = None          # the source's native id (S2 paperId, OpenAlex id)
    arxiv_id: str | None = None
    doi: str | None = None
    year: int | None = None
    authors: tuple[str, ...] = ()
    venue: str | None = None
    abstract: str | None = None
    citation_count: int | None = None
    # Semantic Scholar's "influential citation" flag. On a reference it means the
    # cited work is load-bearing for the source paper; a strong landscape signal.
    is_influential: bool | None = None
    url: str | None = None

    @property
    def has_min_metadata(self) -> bool:
        """Usable for grounding: a non-empty title is the floor."""
        return bool(self.title and self.title.strip())


@dataclass(frozen=True)
class Topic:
    """An OpenAlex topic with its field/domain hierarchy. Used to identify the
    paper's research area for the landscape/trends framing."""

    name: str
    field: str | None = None
    subfield: str | None = None
    domain: str | None = None
    score: float | None = None


@dataclass
class PaperIdentity:
    """Cross-API identity of the uploaded paper.

    Resolution (validated, see DESIGN.md):
      arXiv id -> Semantic Scholar (immediate) -> DOI from externalIds
               -> OpenAlex canonical id via DOI/title (NEVER reuse the MAG id).
    """

    title: str
    arxiv_id: str | None = None
    doi: str | None = None
    s2_paper_id: str | None = None
    openalex_id: str | None = None
    year: int | None = None
    authors: tuple[str, ...] = ()
    abstract: str | None = None
    venue: str | None = None
    citation_count: int | None = None
    topics: list[Topic] = field(default_factory=list)

    @property
    def resolved(self) -> bool:
        """Enough identity to run the retrieval passes."""
        return bool(self.s2_paper_id or self.openalex_id or self.arxiv_id or self.doi)
