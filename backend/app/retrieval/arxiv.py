"""arXiv client.

Role (validated, DESIGN.md): paper + survey search to enrich the landscape and
trends views (e.g. "transformer survey"). Returns Atom XML over HTTPS (http
301-redirects to nowhere useful, so we always use https).

We parse the Atom feed with the stdlib XML parser — no extra dependency.
"""

from __future__ import annotations

import logging
from typing import Any
from xml.etree import ElementTree as ET

from app.retrieval.http import CachedHTTP, RetrievalError
from app.retrieval.types import PaperRef

log = logging.getLogger("paper.retrieval.arxiv")

_BASE = "https://export.arxiv.org/api/query"
_ATOM = "{http://www.w3.org/2005/Atom}"


class Arxiv:
    def __init__(self, http: CachedHTTP) -> None:
        self._http = http

    async def search(self, query: str, *, max_results: int = 10) -> list[PaperRef]:
        """Relevance-ranked arXiv search. ``query`` is a free-text query applied
        to all fields (we wrap it as all:"..." for phrase-ish matching)."""
        try:
            xml = await self._http.get_text(
                _BASE,
                params={
                    "search_query": f'all:"{query}"',
                    "start": 0,
                    "max_results": max_results,
                },
            )
        except RetrievalError as e:
            log.info("arXiv search(%r) failed: %s", query, e)
            return []
        return _parse_feed(xml)


def _parse_feed(xml: str) -> list[PaperRef]:
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as e:
        log.info("arXiv feed parse error: %s", e)
        return []
    out: list[PaperRef] = []
    for entry in root.findall(f"{_ATOM}entry"):
        ref = _entry_to_ref(entry)
        if ref is not None and ref.has_min_metadata:
            out.append(ref)
    return out


def _entry_to_ref(entry: ET.Element) -> PaperRef | None:
    title_el = entry.find(f"{_ATOM}title")
    if title_el is None or not (title_el.text or "").strip():
        return None
    title = " ".join((title_el.text or "").split())

    # arXiv id + abs url live in <id>http://arxiv.org/abs/1234.5678v1</id>.
    id_el = entry.find(f"{_ATOM}id")
    url = (id_el.text or "").strip() if id_el is not None else None
    arxiv_id = _arxiv_id_from_url(url)

    summary_el = entry.find(f"{_ATOM}summary")
    abstract = " ".join((summary_el.text or "").split()) if summary_el is not None else None

    published_el = entry.find(f"{_ATOM}published")
    year = _year_from(published_el.text if published_el is not None else None)

    authors = tuple(
        (a.find(f"{_ATOM}name").text or "").strip()
        for a in entry.findall(f"{_ATOM}author")
        if a.find(f"{_ATOM}name") is not None
    )

    return PaperRef(
        title=title,
        retrieved_from="arxiv",
        arxiv_id=arxiv_id,
        year=year,
        authors=tuple(a for a in authors if a),
        abstract=abstract or None,
        url=url or None,
    )


def _arxiv_id_from_url(url: str | None) -> str | None:
    if not url or "/abs/" not in url:
        return None
    tail = url.rsplit("/abs/", 1)[-1]
    # Strip a trailing version (v1, v2, ...).
    return tail.split("v")[0] if "v" in tail.rsplit(".", 1)[-1] else tail


def _year_from(text: Any) -> int | None:
    if not isinstance(text, str) or len(text) < 4:
        return None
    try:
        return int(text[:4])
    except ValueError:
        return None
