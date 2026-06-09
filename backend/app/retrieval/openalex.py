"""OpenAlex client.

Role (validated, DESIGN.md): the trends backbone + topic identification.

  - resolve_id: get OpenAlex's CANONICAL work id from a DOI or title. We must
    never reuse Semantic Scholar's MAG id (e.g. W2963403868) as an OpenAlex id —
    direct /works/{mag_id} lookups 404. The canonical id (e.g. W2626778328) only
    comes from a DOI lookup or title search.
  - citing_papers: works that cite ours, server-side sorted by cited_by_count
    desc with a year floor + title filter (handles metadata noise, trap #3).
    This is the trends/impact view S2 can't produce.
  - topics: the field/subfield/domain hierarchy for area framing.

OpenAlex inverts abstracts (abstract_inverted_index); we reconstruct plain text.
"""

from __future__ import annotations

import logging
from typing import Any

from app.retrieval.http import CachedHTTP, RetrievalError
from app.retrieval.types import PaperRef, Topic

log = logging.getLogger("paper.retrieval.openalex")

_BASE = "https://api.openalex.org"
_WORK_FIELDS = "id,title,doi,publication_year,cited_by_count,topics,authorships,primary_location,abstract_inverted_index,referenced_works"
_CITE_FIELDS = "id,title,publication_year,cited_by_count,doi,authorships"


class OpenAlex:
    def __init__(self, http: CachedHTTP) -> None:
        self._http = http

    async def resolve_id(
        self, *, doi: str | None = None, title: str | None = None
    ) -> str | None:
        """Resolve the canonical OpenAlex work id. DOI is exact; title search is
        the fallback. Returns the short id (e.g. "W2626778328") or None."""
        if doi:
            obj = await self._get_work(f"https://doi.org/{doi}")
            wid = _short_id(obj.get("id") if obj else None)
            if wid:
                return wid
        if title:
            try:
                data = await self._http.get_json(
                    f"{_BASE}/works",
                    params={"search": title, "per-page": 1, "select": "id,title"},
                )
            except RetrievalError as e:
                log.info("OpenAlex title search failed: %s", e)
                return None
            results = data.get("results", []) if isinstance(data, dict) else []
            if results:
                return _short_id(results[0].get("id"))
        return None

    async def get_work(self, work_id: str) -> dict[str, Any] | None:
        """Full work record by canonical id (caller maps topics/abstract)."""
        return await self._get_work(f"{_BASE}/works/{work_id}", select=_WORK_FIELDS)

    async def topics(self, work_id: str) -> list[Topic]:
        obj = await self.get_work(work_id)
        return _topics_from(obj) if obj else []

    async def citing_papers(
        self, work_id: str, *, year_from: int | None = None, limit: int = 25
    ) -> list[PaperRef]:
        """Works citing ``work_id``, sorted by impact (cited_by_count desc).

        The trends/impact backbone. Applies a year floor and drops title-less
        rows (metadata noise). ``work_id`` may be any OpenAlex-known id including
        legacy MAG ids — the cites filter accepts them even when direct lookup
        wouldn't."""
        filt = f"cites:{work_id}"
        if year_from is not None:
            filt += f",publication_year:>{year_from - 1}"
        try:
            data = await self._http.get_json(
                f"{_BASE}/works",
                params={
                    "filter": filt,
                    "sort": "cited_by_count:desc",
                    "per-page": min(limit, 200),
                    "select": _CITE_FIELDS,
                },
            )
        except RetrievalError as e:
            log.info("OpenAlex citing_papers(%s) failed: %s", work_id, e)
            return []
        results = data.get("results", []) if isinstance(data, dict) else []
        out: list[PaperRef] = []
        for w in results:
            ref = _to_ref(w) if isinstance(w, dict) else None
            if ref is not None and ref.has_min_metadata:
                out.append(ref)
        return out

    async def references(self, work_id: str, *, limit: int = 25) -> list[PaperRef]:
        """The works this paper references (backward citations), impact-sorted.

        The landscape backbone — OpenAlex-only path that doesn't depend on
        Semantic Scholar (which rate-limits hard without a key). We read the
        paper's ``referenced_works`` ids, then batch-fetch their metadata. S2's
        ``isInfluential`` flag is layered on separately by the acquire stage when
        available; here we approximate prior-work importance by citation count."""
        work = await self.get_work(work_id)
        if not work:
            return []
        ref_ids = [
            _short_id(r) for r in (work.get("referenced_works") or [])
        ]
        ref_ids = [r for r in ref_ids if r]
        if not ref_ids:
            return []
        refs = await self.works_by_ids(ref_ids)
        refs.sort(key=lambda r: r.citation_count or 0, reverse=True)
        return refs[:limit]

    async def works_by_ids(
        self, ids: list[str], *, batch_size: int = 50
    ) -> list[PaperRef]:
        """Batch-fetch work metadata for a list of OpenAlex ids via the
        ``openalex_id:a|b|c`` OR-filter (avoids one request per id)."""
        out: list[PaperRef] = []
        for i in range(0, len(ids), batch_size):
            chunk = ids[i : i + batch_size]
            try:
                data = await self._http.get_json(
                    f"{_BASE}/works",
                    params={
                        "filter": "openalex_id:" + "|".join(chunk),
                        "per-page": min(batch_size, 200),
                        "select": _CITE_FIELDS,
                    },
                )
            except RetrievalError as e:
                log.info("OpenAlex works_by_ids batch failed: %s", e)
                continue
            for w in data.get("results", []) if isinstance(data, dict) else []:
                ref = _to_ref(w) if isinstance(w, dict) else None
                if ref is not None and ref.has_min_metadata:
                    out.append(ref)
        return out

    # ----- internals ---------------------------------------------------
    async def _get_work(
        self, url: str, *, select: str | None = None
    ) -> dict[str, Any] | None:
        params = {"select": select} if select else None
        try:
            obj = await self._http.get_json(url, params=params)
        except RetrievalError as e:
            log.info("OpenAlex get_work(%s) failed: %s", url, e)
            return None
        return obj if isinstance(obj, dict) else None


def _short_id(full: Any) -> str | None:
    """OpenAlex ids come as URLs (https://openalex.org/W123). Keep the W-id."""
    if not isinstance(full, str):
        return None
    return full.rsplit("/", 1)[-1] or None


def _topics_from(obj: dict[str, Any]) -> list[Topic]:
    out: list[Topic] = []
    for t in obj.get("topics") or []:
        if not isinstance(t, dict):
            continue
        name = t.get("display_name")
        if not isinstance(name, str):
            continue
        out.append(
            Topic(
                name=name,
                field=_nested(t, "field"),
                subfield=_nested(t, "subfield"),
                domain=_nested(t, "domain"),
                score=t.get("score") if isinstance(t.get("score"), (int, float)) else None,
            )
        )
    return out


def _nested(t: dict[str, Any], key: str) -> str | None:
    v = t.get(key)
    return v.get("display_name") if isinstance(v, dict) else None


def _to_ref(w: dict[str, Any]) -> PaperRef | None:
    title = w.get("title")
    if not isinstance(title, str) or not title.strip():
        return None
    authors = tuple(
        (a.get("author") or {}).get("display_name", "")
        for a in (w.get("authorships") or [])
        if isinstance(a, dict)
    )
    doi = w.get("doi")
    if isinstance(doi, str):
        doi = doi.replace("https://doi.org/", "")
    return PaperRef(
        title=title.strip(),
        retrieved_from="openalex",
        paper_id=_short_id(w.get("id")),
        doi=doi or None,
        year=_opt_int(w.get("publication_year")),
        authors=tuple(a for a in authors if a),
        citation_count=_opt_int(w.get("cited_by_count")),
        abstract=_deinvert(w.get("abstract_inverted_index")),
        url=w.get("id") if isinstance(w.get("id"), str) else None,
    )


def _deinvert(inv: Any) -> str | None:
    """Rebuild plain abstract text from OpenAlex's inverted index
    ({word: [positions]})."""
    if not isinstance(inv, dict) or not inv:
        return None
    positions: list[tuple[int, str]] = []
    for word, idxs in inv.items():
        if isinstance(idxs, list):
            for i in idxs:
                if isinstance(i, int):
                    positions.append((i, word))
    if not positions:
        return None
    positions.sort(key=lambda p: p[0])
    return " ".join(w for _, w in positions)


def _opt_int(v: Any) -> int | None:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None
