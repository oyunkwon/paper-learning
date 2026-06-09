"""Manual end-to-end run of the planner on a real paper PDF.

Not a pytest test (it makes many LLM calls + hits external APIs and takes a
while). Run directly:

    uv run python -m tests.e2e_plan /path/to/paper.pdf

Prints a summary of the produced 4-track curriculum so we can eyeball quality:
track sizes, prereq course groups, grounded source counts, and a spot check that
landscape/trends chapters only cite real retrieved source ids (D2).
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from app.config import load_retrieval_settings, load_settings
from app.llm import build_client
from app.planner.plan import plan_paper
from app.retrieval.http import CachedHTTP


async def _progress(done: int, total: int) -> None:
    print(f"  progress {done}/{total}")


async def main(pdf_path: str) -> None:
    settings = load_settings()
    rcfg = load_retrieval_settings()
    client = build_client(settings)
    http = CachedHTTP(cache_dir=rcfg.cache_dir, contact_email=rcfg.contact_email)
    try:
        curriculum = await plan_paper(
            kind="pdf",
            material_path=Path(pdf_path),
            pages_dir=None,
            client=client,
            model=settings.planner_model,
            http=http,
            on_progress=_progress,
        )
    finally:
        await client.aclose()
        await http.aclose()

    _summarize(curriculum)


def _summarize(c: dict) -> None:
    paper = c["paper"]
    print("\n" + "=" * 70)
    print(f"PAPER: {paper['title']}  ({paper.get('year')})")
    print(f"  arxiv={paper.get('arxivId')} doi={paper.get('doi')}")
    print(f"SOURCES (grounded): {len(c['sources'])}")
    source_ids = {s["id"] for s in c["sources"]}

    for t in c["tracks"]:
        print(f"\n--- TRACK: {t['id']} ({t['kind']}) — {t['title']}")
        if t.get("groups"):
            for g in t["groups"]:
                print(f"  [과목] {g['title']}  교재={g.get('textbook')!r}")
                print(f"         chapters={g['chapterIds']}")
        for ch in t["chapters"]:
            line = f"  · {ch['id']}: {ch['title']}  ({len(ch['concepts'])} concepts)"
            print(line)
            # D2 spot check: grounded tracks must only cite real source ids.
            bad = [s for s in ch.get("sourceIds", []) if s not in source_ids]
            if bad:
                print(f"     !! UNGROUNDED sourceIds: {bad}")

    # Dump full JSON for inspection.
    out = Path("/tmp/paperlearn_curriculum.json")
    out.write_text(json.dumps(c, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nfull curriculum -> {out}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python -m tests.e2e_plan /path/to/paper.pdf")
        raise SystemExit(1)
    asyncio.run(main(sys.argv[1]))
