"""Tolerant JSON extraction from LLM output.

Ported from `learning`'s curriculum parser: models wrap JSON in prose/fences and
emit common defects (trailing commas, missing commas, smart quotes). Every
planner stage that asks for strict JSON parses through here.
"""

from __future__ import annotations

import json
import re
from typing import Any


class JSONParseError(RuntimeError):
    """Raised when no usable JSON object can be extracted from model output."""


def parse_json_object(raw: str) -> dict[str, Any]:
    """Extract a JSON object from model output, tolerant of fences/prose/defects."""
    obj = _parse(raw)
    if not isinstance(obj, dict):
        raise JSONParseError("최상위가 JSON 객체가 아닙니다.")
    return obj


def _parse(raw: str) -> Any:
    s = raw.strip()
    if s.startswith("```"):
        parts = s.split("```", 2)
        s = parts[1] if len(parts) > 1 else raw
        if s.startswith("json"):
            s = s[len("json") :]
        s = s.strip()

    start, end = s.find("{"), s.rfind("}")
    candidate = s[start : end + 1] if (start != -1 and end > start) else s

    for attempt in (candidate, _repair(candidate)):
        try:
            return json.loads(attempt)
        except json.JSONDecodeError:
            continue
    try:
        return json.loads(_repair(candidate))
    except json.JSONDecodeError as e:
        raise JSONParseError(f"JSON 파싱 실패: {e}") from e


def _repair(s: str) -> str:
    """Conservative repair of unambiguous LLM JSON defects."""
    s = s.translate({0x201C: 0x22, 0x201D: 0x22, 0x2018: 0x27, 0x2019: 0x27})
    s = re.sub(r",(\s*[}\]])", r"\1", s)                                  # trailing comma
    s = re.sub(r'(["\d\]}])(\s*\n\s*)("[^"\n]*"\s*:)', r"\1,\2\3", s)     # missing comma before key
    s = re.sub(r'([}\]])(\s*\n\s*)([{\[])', r"\1,\2\3", s)               # missing comma before element
    return s
