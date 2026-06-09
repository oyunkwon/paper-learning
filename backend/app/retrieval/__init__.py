"""Retrieval layer: grounded external sources for the landscape / trends /
prerequisite tracks.

Every external citation the system shows MUST originate from a real API
response here (design decision D2: no hallucinated references). The LLM only
summarizes retrieved metadata; it never invents titles, authors, or DOIs.

Modules:
  types      shared dataclasses (PaperRef, PaperIdentity, ...)
  http       cached async HTTP helper shared by all clients
  semantic_scholar   identify + references (isInfluential) -> landscape backbone
  openalex   citing papers (sorted by impact) + topics -> trends backbone
  arxiv      paper / survey search
  identify   resolve an uploaded paper to a stable cross-API identity

Validation results and API role split are documented in docs/DESIGN.md.
"""
