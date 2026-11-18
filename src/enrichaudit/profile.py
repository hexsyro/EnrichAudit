"""Dataset profiling: generate a JSON-serializable contract from raw rows.

The contract is deliberately descriptive, not prescriptive: it records what is
in a table (types, counts, ranges, top values, freshness) so a pipeline can
diff exports or use the shape as a starting point for its own checks.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime
from typing import Any

from enrichaudit.common import as_naive, as_str, parse_date, to_float

_BOOL_TOKENS = {"true", "yes", "y", "1", "false", "no", "n", "0"}
_NUMBER_RE = re.compile(r"^[+-]?\d+(?:\.\d+)?\s*[kKmMbB%]?$")


def _looks_numeric(value: Any) -> bool:
    """Stricter than ``to_float``: 'cat-0' must not become a number column."""
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return True
    return bool(_NUMBER_RE.fullmatch(as_str(value).replace(",", "").strip()))


def _guess_type(values: list[Any]) -> str:
    """Guess one of text / number / date / bool from the non-null values."""
    if not values:
        return "text"
    if all(isinstance(value, bool) for value in values):
        return "bool"
    if all(isinstance(value, str) and value.strip().lower() in _BOOL_TOKENS for value in values):
        return "bool"
    if all(parse_date(value) is not None for value in values) and all(
        any(sep in as_str(value) for sep in ("-", "/")) for value in values
    ):
        return "date"
    if all(_looks_numeric(value) and to_float(value) is not None for value in values):
        return "number"
    return "text"


def _percentile(values: list[float], pct: float) -> float | None:
    """Linear-interpolation percentile; ``None`` when there is no data."""
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 4)
    position = (len(ordered) - 1) * pct / 100.0
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    value = ordered[lower] + (ordered[upper] - ordered[lower]) * fraction
    return round(value, 4)


def build_contract(rows: list[dict]) -> dict[str, Any]:
    """Profile ``rows`` into a JSON-serializable per-column contract.

    Numeric columns get min/max and p50/p95; date columns get freshness
    (min/max parsed date); categorical columns get up to 10 top values.
    ``null_rate`` is a fraction of all rows (0.0-1.0), not a percentage.
    """
    columns: dict[str, list[Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        for key, value in row.items():
            columns.setdefault(str(key), []).append(value)

    total = len(rows)
    profiled: dict[str, dict[str, Any]] = {}
    for name, values in columns.items():
        non_null = [value for value in values if value is not None and as_str(value) != ""]
        null_count = len(values) - len(non_null)
        kind = _guess_type(non_null)
        entry: dict[str, Any] = {
            "type": kind,
            "count": len(non_null),
            "null_rate": round(null_count / total, 4) if total else 0.0,
            "distinct_count": len({as_str(value).strip() for value in non_null}),
        }
        if kind == "number":
            numbers = [number for value in non_null if (number := to_float(value)) is not None]
            if numbers:
                entry.update(
                    {
                        "min": round(min(numbers), 4),
                        "max": round(max(numbers), 4),
                        "p50": _percentile(numbers, 50),
                        "p95": _percentile(numbers, 95),
                    }
                )
        elif kind == "date":
            dates: list[datetime] = []
            for value in non_null:
                parsed = parse_date(value)
                if parsed is None:
                    continue
                naive = as_naive(parsed)
                if naive is not None:
                    dates.append(naive)
            if dates:
                entry["freshness"] = {
                    "min": min(dates).date().isoformat(),
                    "max": max(dates).date().isoformat(),
                }
        else:
            counts = Counter(as_str(value).strip() for value in non_null)
            top = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:10]
            entry["top_values"] = [{"value": value, "count": count} for value, count in top]
        profiled[name] = entry

    return {"row_count": total, "column_count": len(columns), "columns": profiled}
