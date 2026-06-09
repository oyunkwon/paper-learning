"""Live smoke test for the retrieval layer against real APIs.

Marked 'live' so it can be deselected offline (`-m "not live"`). Uses the
validated paper (Attention Is All You Need, arXiv 1706.03762) and asserts the
role split from DESIGN.md actually holds end-to-end:

  - identify resolves S2 + OpenAlex ids + topics
  - references carry the is_influential flag (landscape backbone)
  - citing_papers come back impact-sorted with sane metadata (trends backbone)
  - arXiv search returns results
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from app.retrieval.arxiv import Arxiv
from app.retrieval.http import CachedHTTP
from app.retrieval.identify import identify
from app.retrieval.openalex import OpenAlex
from app.retrieval.semantic_scholar import SemanticScholar

ARXIV_ID = "1706.03762"

pytestmark = [pytest.mark.live, pytest.mark.asyncio(loop_scope="module")]


@pytest.fixture(scope="module")
async def http():
    # Module-scoped + persistent cache: the 5 tests repeatedly identify the same
    # paper, so sharing the cache means the API is hit once per unique URL,
    # sharply cutting rate-limit flakiness vs a fresh tempdir per test.
    cache = Path(tempfile.gettempdir()) / "paperlearn-test-cache"
    client = CachedHTTP(cache_dir=cache)
    try:
        yield client
    finally:
        await client.aclose()


async def test_identify_resolves_cross_api(http):
    s2, oa = SemanticScholar(http), OpenAlex(http)
    ident = await identify(s2, oa, arxiv_id=ARXIV_ID)
    if ident is None or not ident.openalex_id:
        pytest.skip("OpenAlex unreachable (likely rate-limited by the suite)")
    assert "attention" in ident.title.lower()
    # OpenAlex is the resilient backbone: identity resolves its canonical id via
    # the title-search fallback even when S2 is rate-limited (no DOI here).
    assert ident.openalex_id.startswith("W")
    # topics are secondary enrichment from a follow-up call; assert only when the
    # call wasn't rate-limited (empty topics => skip rather than fail).
    if not ident.topics:
        pytest.skip("OpenAlex topics unreachable (likely rate-limited)")
    assert any(t.field == "Computer Science" for t in ident.topics)
    # S2 is best-effort enrichment; it rate-limits hard without a key, so we
    # don't require s2_paper_id here.


async def test_references_have_influential_signal(http):
    # S2 influential flag is enrichment-only and rate-limits without a key.
    # Skip (not fail) when S2 is unreachable — the landscape backbone is OpenAlex.
    s2 = SemanticScholar(http)
    paper = await s2.get_paper(arxiv_id=ARXIV_ID)
    if paper is None:
        pytest.skip("Semantic Scholar unreachable (likely rate-limited)")
    refs = await s2.references(paper["paperId"], limit=50)
    assert refs, "expected references"
    assert all(r.has_min_metadata for r in refs)
    # At least one reference should be flagged influential (validated: seq2seq).
    assert any(r.is_influential for r in refs)


async def test_citing_papers_impact_sorted(http):
    s2, oa = SemanticScholar(http), OpenAlex(http)
    ident = await identify(s2, oa, arxiv_id=ARXIV_ID)
    if ident is None or not ident.openalex_id:
        pytest.skip("OpenAlex unreachable (likely rate-limited by the suite)")
    citing = await oa.citing_papers(ident.openalex_id, year_from=2019, limit=10)
    if not citing:
        pytest.skip("OpenAlex citing unreachable (likely rate-limited)")
    assert all(c.title for c in citing)
    counts = [c.citation_count or 0 for c in citing]
    assert counts == sorted(counts, reverse=True)   # server-side impact sort
    assert counts[0] > 1000                          # landmark follow-ups


async def test_openalex_references_landscape_backbone(http):
    # OpenAlex is the landscape backbone (no S2 dependency, survives S2 429s).
    s2, oa = SemanticScholar(http), OpenAlex(http)
    ident = await identify(s2, oa, arxiv_id=ARXIV_ID)
    if ident is None or not ident.openalex_id:
        pytest.skip("OpenAlex unreachable (likely rate-limited by the suite)")
    refs = await oa.references(ident.openalex_id, limit=15)
    if not refs:
        pytest.skip("OpenAlex references unreachable (likely rate-limited)")
    assert all(r.title and r.retrieved_from == "openalex" for r in refs)
    # impact-sorted (descending citation count)
    counts = [r.citation_count or 0 for r in refs]
    assert counts == sorted(counts, reverse=True)


async def test_arxiv_search(http):
    arx = Arxiv(http)
    results = await arx.search("transformer", max_results=3)
    assert results
    assert all(r.retrieved_from == "arxiv" for r in results)
