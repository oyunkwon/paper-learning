"""Unit tests for Stage 0 ingest identifier extraction (no network)."""

from __future__ import annotations

from app.planner.ingest import ingest_text


def test_arxiv_prefixed():
    assert ingest_text("see arXiv:1706.03762 for details").arxiv_id == "1706.03762"


def test_arxiv_with_version():
    assert ingest_text("arXiv:2005.14165v3").arxiv_id == "2005.14165"


def test_arxiv_url():
    assert ingest_text("http://arxiv.org/abs/2005.14165 GPT-3").arxiv_id == "2005.14165"
    assert ingest_text("https://arxiv.org/pdf/1810.04805").arxiv_id == "1810.04805"


def test_bare_decimal_not_matched_without_prefix():
    # A bare YYMM.NNNNN without "arXiv:" or a URL must NOT be picked up (avoids
    # matching random decimals like a version "1.03762" in body text).
    assert ingest_text("the value was 1706.03762 in the table").arxiv_id is None


def test_doi_extracted_and_trimmed():
    assert ingest_text("DOI: 10.1038/nature14539.").doi == "10.1038/nature14539"
    assert ingest_text("https://doi.org/10.1145/3292500").doi == "10.1145/3292500"


def test_empty_text():
    ing = ingest_text("")
    assert ing.arxiv_id is None and ing.doi is None
