"""json_extract.py — the one JSON OBJECT out of a model's reply (2026-10-25).

`modules/intake/analysis.py` and `consult_compose.py` each carried a byte-identical `_extract_json`: a
model that wraps its JSON in prose or code fences still gives one object; anything else is `{}`.

Not this: `chart_summary._extract_json` (answers `None`, accepts any JSON value, strips fences only at
the ends) and `ai_referral.extract_json` (a photo can hold several documents — it picks the likeliest
among a list). `tests/test_shared_helpers_contract.py` pins all of them.
"""

import json
import re


def extract_object(text):
    """The JSON object inside `text`, tolerating prose and ``` fences around it; `{}` when there is none."""
    if not text:
        return {}
    t = text.strip()
    if "```" in t:
        m = re.search(r"```(?:json)?\s*([\s\S]*?)```", t, re.I)
        if m:
            t = m.group(1).strip()
    start, end = t.find("{"), t.rfind("}")
    if start == -1 or end == -1 or end < start:
        return {}
    try:
        parsed = json.loads(t[start:end + 1])
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}
