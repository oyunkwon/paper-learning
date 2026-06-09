"""Material handling: turn an uploaded file into LLM-ready content.

PDF -> rendered page JPEGs (via pymupdf) -> base64 image_url content blocks.
MD  -> raw text content block.

Page images are JPEG with a bounded long edge so the base64 payload stays well
under the relay request-size limit (PNG at full zoom is ~5x larger and 413s).
The planning pass batches pages by a payload budget; the tutor only ever sends
the active chapter's page range.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import fitz  # pymupdf

# Render target: longest page edge in pixels, and JPEG quality. ~230KB base64
# per page -> a payload budget can fit ~15 pages/request.
_LONG_EDGE_PX = 1400
_JPEG_QUALITY = 80
_MAX_ZOOM = 3.0

# Per-request base64 budget for image payloads (bytes). Relay 413s well above
# this; stay conservative to leave room for JSON + text overhead.
PAGE_PAYLOAD_BUDGET = 3_500_000


class MaterialError(RuntimeError):
    """Raised when material can't be processed."""


def _zoom_for(page: "fitz.Page") -> float:
    rect = page.rect
    long_pt = max(rect.width, rect.height) or 1.0
    return min(_LONG_EDGE_PX / long_pt, _MAX_ZOOM)


def render_pdf_pages(pdf_path: Path, pages_dir: Path) -> int:
    """Render every PDF page to pages_dir/page-NNN.jpg. Returns page count."""
    pages_dir.mkdir(parents=True, exist_ok=True)
    try:
        doc = fitz.open(pdf_path)
    except Exception as e:  # pymupdf raises bare Exceptions
        raise MaterialError(f"PDF를 열 수 없습니다: {e}") from e
    try:
        count = doc.page_count
        for i in range(count):
            page = doc.load_page(i)
            zoom = _zoom_for(page)
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
            (pages_dir / f"page-{i + 1:03d}.jpg").write_bytes(
                pix.tobytes("jpeg", jpg_quality=_JPEG_QUALITY)
            )
        return count
    finally:
        doc.close()


def list_page_files(pages_dir: Path) -> list[Path]:
    if not pages_dir.exists():
        return []
    return sorted(pages_dir.glob("page-*.jpg"))


def page_count(pages_dir: Path) -> int:
    return len(list_page_files(pages_dir))


def _page_path(pages_dir: Path, idx: int) -> Path:
    return pages_dir / f"page-{idx:03d}.jpg"


def _jpg_to_data_url(path: Path) -> str:
    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:image/jpeg;base64,{b64}"


def _b64_len(n_bytes: int) -> int:
    return ((n_bytes + 2) // 3) * 4


def image_block(path: Path) -> dict[str, Any]:
    return {"type": "image_url", "image_url": {"url": _jpg_to_data_url(path)}}


def text_block(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}


def pdf_page_blocks(
    pages_dir: Path, *, start: int | None = None, end: int | None = None
) -> list[dict[str, Any]]:
    """Image content blocks for pages [start, end] (1-indexed, inclusive).

    Each image is preceded by a small text label so the model knows the page
    number it's looking at. None bounds mean "from the first / to the last".
    """
    files = list_page_files(pages_dir)
    if not files:
        return []
    lo = 1 if start is None else max(1, start)
    hi = len(files) if end is None else min(len(files), end)
    blocks: list[dict[str, Any]] = []
    for idx in range(lo, hi + 1):
        path = _page_path(pages_dir, idx)
        if path.exists():
            blocks.append(text_block(f"[{idx}페이지]"))
            blocks.append(image_block(path))
    return blocks


def pdf_page_blocks_capped(
    pages_dir: Path,
    *,
    start: int | None = None,
    end: int | None = None,
    budget: int = PAGE_PAYLOAD_BUDGET,
) -> tuple[list[dict[str, Any]], int, int]:
    """Like pdf_page_blocks but stops once the base64 payload would exceed
    `budget`. Returns (blocks, included_start, included_end_actual). Guarantees
    a chat turn never 413s even if a chapter spans many pages."""
    files = list_page_files(pages_dir)
    if not files:
        return [], 0, 0
    lo = 1 if start is None else max(1, start)
    hi = len(files) if end is None else min(len(files), end)
    blocks: list[dict[str, Any]] = []
    acc = 0
    last = lo - 1
    for idx in range(lo, hi + 1):
        path = _page_path(pages_dir, idx)
        if not path.exists():
            continue
        size = _b64_len(path.stat().st_size)
        if acc > 0 and acc + size > budget:
            break
        blocks.append(text_block(f"[{idx}페이지]"))
        blocks.append(image_block(path))
        acc += size
        last = idx
    return blocks, lo, last


def page_batches(
    pages_dir: Path, *, budget: int = PAGE_PAYLOAD_BUDGET
) -> list[tuple[int, int]]:
    """Split pages into (start, end) ranges whose base64 payload stays under
    `budget`. A single oversized page still gets its own batch."""
    files = list_page_files(pages_dir)
    if not files:
        return []
    batches: list[tuple[int, int]] = []
    batch_start = 1
    acc = 0
    for i, path in enumerate(files, start=1):
        size = _b64_len(path.stat().st_size)
        if acc > 0 and acc + size > budget:
            batches.append((batch_start, i - 1))
            batch_start = i
            acc = 0
        acc += size
    batches.append((batch_start, len(files)))
    return batches


def read_md(md_path: Path) -> str:
    try:
        return md_path.read_text(encoding="utf-8")
    except OSError as e:
        raise MaterialError(f"자료를 읽을 수 없습니다: {e}") from e


# Below this many extracted characters per page (averaged over the range) we
# treat the PDF as a scan/image with no usable text layer and fall back to
# sending page images instead.
_MIN_CHARS_PER_PAGE = 80


def pdf_page_text(
    pdf_path: Path, *, start: int | None = None, end: int | None = None
) -> str:
    """Extract the text layer for pages [start, end] (1-indexed, inclusive) from
    the original PDF, each page prefixed with a "[N페이지]" label.

    Text is ~100x lighter than a page image, so a whole chapter — however many
    pages — fits in one request with no truncation. Returns "" when the PDF has
    no usable text (a scan); callers then fall back to page images. Cheap enough
    (<1s even for hundreds of pages) to run per turn without caching.
    """
    try:
        doc = fitz.open(pdf_path)
    except Exception as e:  # pymupdf raises bare Exceptions
        raise MaterialError(f"PDF를 열 수 없습니다: {e}") from e
    try:
        n = doc.page_count
        lo = 1 if start is None else max(1, start)
        hi = n if end is None else min(n, end)
        if hi < lo:
            return ""
        parts: list[str] = []
        total_chars = 0
        for i in range(lo, hi + 1):
            text = doc.load_page(i - 1).get_text("text").strip()
            total_chars += len(text)
            if text:
                parts.append(f"[{i}페이지]\n{text}")
        # Scan/image PDF (or text-empty range): signal "no text" to the caller.
        span = hi - lo + 1
        if span > 0 and total_chars < _MIN_CHARS_PER_PAGE * span:
            return ""
        return "\n\n".join(parts)
    finally:
        doc.close()
