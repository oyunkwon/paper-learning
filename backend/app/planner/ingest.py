"""Stage 0: Ingest — pull identifiers from the document.

Cheap, no-LLM extraction of an arXiv id / DOI from the PDF's text layer, used to
identify the paper against the retrieval APIs (Stage 2). The title is best taken
from the comprehend pass (more reliable than guessing the first big line), so we
only extract ids here.

arXiv ids come in two schemes:
  - new:  YYMM.NNNNN  (optionally vN), e.g. 1706.03762
  - old:  archive/YYMMNNN, e.g. math.GT/0309136
DOIs match the standard 10.xxxx/... pattern.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from app import material

log = logging.getLogger("paper.planner.ingest")

# arXiv:1706.03762 / arXiv:1706.03762v5 / bare 1706.03762
_ARXIV_NEW = re.compile(
    r"(?:arXiv:\s*)?(\d{4}\.\d{4,5})(?:v\d+)?", re.IGNORECASE
)
# arXiv abs/pdf URL
_ARXIV_URL = re.compile(
    r"arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5})", re.IGNORECASE
)
# DOI, optionally as a doi.org URL. Stop at whitespace/closing punctuation.
_DOI = re.compile(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+", re.IGNORECASE)


@dataclass
class Ingested:
    arxiv_id: str | None = None
    doi: str | None = None


def ingest_pdf(pdf_path: Path, *, head_pages: int = 2) -> Ingested:
    """Extract identifiers from the first ``head_pages`` of a PDF (ids live on the
    first page / header). Returns empty Ingested if there's no text layer."""
    try:
        text = material.pdf_page_text(pdf_path, start=1, end=head_pages)
    except material.MaterialError:
        return Ingested()
    return ingest_text(text)


def ingest_text(text: str) -> Ingested:
    if not text:
        return Ingested()
    return Ingested(
        arxiv_id=_find_arxiv(text),
        doi=_clean_doi(_find_first(_DOI, text)),
    )


def _find_arxiv(text: str) -> str | None:
    m = _ARXIV_URL.search(text)
    if m:
        return m.group(1)
    # Only trust a bare YYMM.NNNNN when prefixed by "arXiv:" to avoid matching
    # random decimals; the URL form is already handled above.
    m = re.search(r"arXiv:\s*(\d{4}\.\d{4,5})(?:v\d+)?", text, re.IGNORECASE)
    return m.group(1) if m else None


def _find_first(pattern: re.Pattern[str], text: str) -> str | None:
    m = pattern.search(text)
    return m.group(0) if m else None


def _clean_doi(doi: str | None) -> str | None:
    if not doi:
        return None
    # Trim trailing punctuation that regex greedily caught.
    return doi.rstrip(".,;)")
