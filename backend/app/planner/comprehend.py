"""Stage 1: Comprehend — the paper-only pass.

Reads the paper and produces the comprehension object: the presupposed vs
introduced concept split (the axis, D4) plus motivation/claims/method/results/
limitations/insights for the paper track.

Strategy mirrors the tutor's material handling: prefer the PDF text layer (cheap,
the whole paper fits in one request), fall back to page-image batches with a
map-merge only when there's no usable text (a scan). Markdown is single-shot.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app import material
from app.jsonparse import JSONParseError, parse_json_object
from app.llm import LLMClient
from app.planner.prompts import COMPREHEND_SYSTEM, comprehend_user

log = logging.getLogger("paper.planner.comprehend")


@dataclass
class Comprehension:
    """Structured output of the paper-only pass."""

    title: str
    presupposed: list[dict[str, str]] = field(default_factory=list)
    introduced: list[dict[str, str]] = field(default_factory=list)
    motivation: str = ""
    claims: list[str] = field(default_factory=list)
    method: str = ""
    results: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    insights: list[str] = field(default_factory=list)

    @property
    def presupposed_concepts(self) -> list[str]:
        """Concept names only — the prereq frontier seed."""
        return [p["concept"] for p in self.presupposed if p.get("concept")]


async def comprehend(
    *,
    client: LLMClient,
    model: str,
    kind: str,
    material_path,
    pages_dir=None,
) -> Comprehension:
    """Run the comprehend pass over a paper. ``kind`` is "pdf" or "md"."""
    if kind == "md":
        text = material.read_md(material_path)
        obj = await _run(client, model, [material.text_block(
            "다음 논문 전문이다:\n\n" + text
        )])
        return _to_comprehension(obj)

    # PDF: prefer the text layer (whole paper, one request).
    page_text = ""
    try:
        page_text = material.pdf_page_text(material_path)
    except material.MaterialError:
        page_text = ""

    if page_text:
        obj = await _run(client, model, [material.text_block(
            "다음은 논문의 원문 텍스트다:\n\n" + page_text
        )])
        return _to_comprehension(obj)

    # Scan / no text layer: map over image batches, then merge.
    return await _comprehend_image_batches(client, model, pages_dir)


async def _comprehend_image_batches(client, model, pages_dir) -> Comprehension:
    if pages_dir is None:
        raise JSONParseError("페이지 이미지를 찾을 수 없습니다.")
    batches = material.page_batches(pages_dir)
    if not batches:
        raise JSONParseError("논문 페이지를 읽지 못했습니다.")
    partials: list[Comprehension] = []
    for start, end in batches:
        blocks = material.pdf_page_blocks(pages_dir, start=start, end=end)
        if not blocks:
            continue
        try:
            obj = await _run(
                client, model,
                [material.text_block(comprehend_user(start, end)), *blocks],
            )
            partials.append(_to_comprehension(obj))
        except JSONParseError:
            log.warning("comprehend batch p%d-%d unparsable, skipped", start, end)
    if not partials:
        raise JSONParseError("논문을 분석하지 못했습니다.")
    return _merge(partials)


async def _run(client: LLMClient, model: str, blocks: list[dict[str, Any]]) -> dict[str, Any]:
    messages = [
        {"role": "system", "content": COMPREHEND_SYSTEM},
        {"role": "user", "content": [material.text_block(comprehend_user()), *blocks]},
    ]
    raw = await client.complete(model=model, messages=messages, temperature=0.1)
    return parse_json_object(raw)


def _to_comprehension(obj: dict[str, Any]) -> Comprehension:
    def _concept_list(key: str, name_key: str) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        for item in obj.get(key, []) or []:
            if isinstance(item, dict) and item.get("concept"):
                out.append({
                    "concept": str(item["concept"]),
                    name_key: str(item.get(name_key, "")),
                })
            elif isinstance(item, str) and item.strip():
                out.append({"concept": item.strip(), name_key: ""})
        return out

    def _str_list(key: str) -> list[str]:
        return [str(x) for x in (obj.get(key) or []) if str(x).strip()]

    return Comprehension(
        title=str(obj.get("title", "논문")),
        presupposed=_concept_list("presupposed", "why"),
        introduced=_concept_list("introduced", "what"),
        motivation=str(obj.get("motivation", "")),
        claims=_str_list("claims"),
        method=str(obj.get("method", "")),
        results=_str_list("results"),
        limitations=_str_list("limitations"),
        insights=_str_list("insights"),
    )


def _merge(parts: list[Comprehension]) -> Comprehension:
    """Merge per-batch comprehensions: dedup concepts by name, concatenate the
    narrative fields. Title from the first non-empty."""
    seen_pre: dict[str, dict[str, str]] = {}
    seen_int: dict[str, dict[str, str]] = {}
    for p in parts:
        for c in p.presupposed:
            seen_pre.setdefault(c["concept"].lower(), c)
        for c in p.introduced:
            seen_int.setdefault(c["concept"].lower(), c)

    def _join(field_name: str) -> str:
        vals = [getattr(p, field_name) for p in parts if getattr(p, field_name)]
        return " ".join(vals)

    def _concat(field_name: str) -> list[str]:
        out: list[str] = []
        for p in parts:
            for x in getattr(p, field_name):
                if x not in out:
                    out.append(x)
        return out

    title = next((p.title for p in parts if p.title and p.title != "논문"), "논문")
    return Comprehension(
        title=title,
        presupposed=list(seen_pre.values()),
        introduced=list(seen_int.values()),
        motivation=_join("motivation"),
        claims=_concat("claims"),
        method=_join("method"),
        results=_concat("results"),
        limitations=_concat("limitations"),
        insights=_concat("insights"),
    )
