"""
Deterministic parsing of the final answer lines the forecast prompts ask for.
No LLM call: a sample whose answer cannot be parsed raises ValueError and is dropped,
and the median is taken over the samples that parsed.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

PERCENTILES = (10, 20, 40, 60, 80, 90)
_NUM = re.compile(r"-?\d+(?:\.\d+)?")


def parse_probability(text: str) -> float:
    hits = re.findall(r"Probability\s*:\s*\**\s*(\d+(?:\.\d+)?)\s*%", text, flags=re.I)
    if not hits:
        raise ValueError("no 'Probability: ZZ%' line found")
    value = float(hits[-1]) / 100
    if not 0 <= value <= 1:
        raise ValueError(f"probability out of range: {hits[-1]}%")
    return value


def parse_options(text: str, options: list[str]) -> dict[str, float]:
    """Last 'Option: P%' line per option; values are normalised to sum to 1."""
    found: dict[str, float] = {}
    for opt in options:
        pattern = rf"^[\s*\-#>]*(?:Option\s*)?\"?{re.escape(opt)}\"?\**\s*[:=\-–]\s*\**\s*(\d+(?:\.\d+)?)\s*%?"
        hits = re.findall(pattern, text, flags=re.I | re.M)
        if not hits:
            raise ValueError(f"no probability line for option {opt!r}")
        found[opt] = float(hits[-1])
    total = sum(found.values())
    scale = 100 if total > 1.5 else 1
    if not 0.9 * scale <= total <= 1.1 * scale:
        raise ValueError(f"option probabilities sum to {total}, expected about {scale}")
    return {k: v / total for k, v in found.items()}


def _clean_number(raw: str) -> float:
    s = raw.replace(",", "").replace("$", "").replace("€", "").strip()
    m = _NUM.search(s)
    if not m:
        raise ValueError(f"no number in {raw!r}")
    return float(m.group())


def _last_percentile_lines(text: str) -> dict[int, str]:
    lines: dict[int, str] = {}
    for p in PERCENTILES:
        hits = re.findall(rf"Percentile\s*{p}\s*:\s*([^\n]+)", text, flags=re.I)
        if not hits:
            raise ValueError(f"no 'Percentile {p}:' line found")
        lines[p] = hits[-1]
    return lines


def parse_percentiles(text: str) -> list[tuple[float, float]]:
    """[(0.1, value), ...] strictly increasing in value."""
    values = [(p / 100, _clean_number(v)) for p, v in _last_percentile_lines(text).items()]
    if any(b[1] <= a[1] for a, b in zip(values, values[1:])):
        raise ValueError(f"percentile values not increasing: {values}")
    return values


def parse_date_percentiles(text: str) -> list[tuple[float, float]]:
    """[(0.1, unix_timestamp), ...] strictly increasing."""
    out = []
    for p, raw in _last_percentile_lines(text).items():
        m = re.search(r"(\d{4}-\d{2}-\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?)?", raw)
        if not m:
            raise ValueError(f"no YYYY-MM-DD date in {raw!r}")
        y, mo, d = map(int, m.group(1).split("-"))
        hh, mm, ss = (int(x) if x else 0 for x in m.group(2, 3, 4))
        out.append((p / 100, datetime(y, mo, d, hh, mm, ss, tzinfo=timezone.utc).timestamp()))
    if any(b[1] <= a[1] for a, b in zip(out, out[1:])):
        raise ValueError("date percentiles not increasing")
    return out
