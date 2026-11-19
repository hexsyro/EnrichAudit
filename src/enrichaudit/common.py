"""Shared helpers for EnrichAudit: row coercion, dataset loading, result shape.

The rules in ``logic.py`` receive the rows of a dataset (a list of dicts) plus
an options dict and return a :class:`ToolResult`. The helpers here normalize
the messy real-world columns pipelines actually export — mixed casing,
trailing spaces, ``-`` vs ``_`` — so the rules can be defensive about field
presence without repeating lookup code.
"""

from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

SEVERITY_RANK = {"low": 1, "medium": 2, "high": 3}
# Bumped when the shape of ToolResult.summary changes in a way consumers must notice.
RESULT_SCHEMA_VERSION = "1.1"


@dataclass
class ToolResult:
    status: str = "success"
    findings: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Column lookup
# ---------------------------------------------------------------------------

_SEP_RE = re.compile(r"[^0-9a-z]+")


def norm_key(key: Any) -> str:
    """Normalize a column name for lookup: lowercase, strip, dashes -> underscore."""
    return _SEP_RE.sub("_", str(key).strip().lower())


def index_row(row: dict) -> dict[str, Any]:
    """Return a row indexed by normalized column name (first occurrence wins)."""
    indexed: dict[str, Any] = {}
    for key, value in row.items():
        normalized = norm_key(key)
        if normalized and normalized not in indexed:
            indexed[normalized] = value
    return indexed


def get(row: dict, keys: list[str], default: Any = None) -> Any:
    """Case-insensitive lookup for the first present non-null column among ``keys``."""
    indexed = index_row(row)
    for key in keys:
        value = indexed.get(norm_key(key))
        if value is not None:
            return value
    return default


def as_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return str(value)
    return str(value).strip()


def to_float(value: Any, default: float | None = None) -> float | None:
    """Parse a number from a messy cell (commas, ``k``/``M``/``b`` suffixes).

    A trailing percent sign is *ignored*, not converted: ``"12%"`` parses as
    ``12.0``. Engagement rates in this tool are therefore expressed in
    percentage points (0-100), never as fractions (0-1). Rejects date-like or
    ambiguous strings instead of silently grabbing the first number group.
    """
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return float(value)
    text = as_str(value).replace(",", "").strip()
    if not text:
        return default
    multiplier = 1.0
    if text.endswith(("k", "K")):
        multiplier = 1_000.0
        text = text[:-1]
    elif text.endswith(("m", "M")):
        multiplier = 1_000_000.0
        text = text[:-1]
    elif text.endswith(("b", "B")):
        multiplier = 1_000_000_000.0
        text = text[:-1]
    groups = re.findall(r"-?\d+(?:\.\d+)?", text)
    if len(groups) != 1:
        return default
    try:
        return float(groups[0]) * multiplier
    except ValueError:
        return default


def find_id(row: dict) -> str | None:
    """Best-effort record id for a row."""
    value = get(row, ["id", "record_id", "post_id", "uuid", "row_id", "key"])
    if value is None:
        return None
    text = as_str(value)
    return text or None


# ---------------------------------------------------------------------------
# Dates
# ---------------------------------------------------------------------------

_DATE_FORMATS = (
    "%Y-%m-%d",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y/%m/%d",
    "%Y/%m/%d %H:%M:%S",
    "%m/%d/%Y",
    "%m/%d/%Y %H:%M:%S",
    "%d/%m/%Y",
    "%d/%m/%Y %H:%M:%S",
    "%b %d, %Y",
    "%d %b %Y",
    "%Y-%m-%d %H:%M",
)


def parse_date(value: Any) -> datetime | None:
    """Parse a date-ish cell into a datetime (tz-aware values are kept aware)."""
    if isinstance(value, datetime):
        return value
    if value is None:
        return None
    text = as_str(value).strip()
    if not text:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def as_naive(value: datetime | None) -> datetime | None:
    """Drop tzinfo so aware and naive timestamps compare safely."""
    if value is None:
        return None
    return value.replace(tzinfo=None) if value.tzinfo is not None else value


def _bad_option(key: str, value: Any, expected: str) -> ValueError:
    return ValueError(f"invalid value for option '{key}': expected {expected}, got {type(value).__name__} ({value!r})")


def opt_float(options: dict, key: str, default: float, *, strict: bool = False) -> float:
    """Coerce an option to float. ``strict=True`` raises instead of falling back."""
    if key not in options:
        return default
    value = options[key]
    if isinstance(value, bool):
        if strict:
            raise _bad_option(key, value, "number")
        return default
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            pass
    if strict:
        raise _bad_option(key, value, "number")
    return default


def opt_int(options: dict, key: str, default: int, *, strict: bool = False) -> int:
    """Coerce an option to int. ``strict=True`` raises instead of falling back."""
    if key not in options:
        return default
    value = options[key]
    if isinstance(value, bool):
        if strict:
            raise _bad_option(key, value, "integer")
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            pass
    if strict:
        raise _bad_option(key, value, "integer")
    return default


def opt_bool(options: dict, key: str, default: bool, *, strict: bool = False) -> bool:
    """Coerce an option to bool. ``strict=True`` raises instead of falling back."""
    if key not in options:
        return default
    value = options[key]
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"true", "yes", "1", "on"}:
            return True
        if text in {"false", "no", "0", "off"}:
            return False
    if strict:
        raise _bad_option(key, value, "boolean")
    return default


def opt_str(options: dict, key: str, default: str | None = None, *, strict: bool = False) -> str | None:
    """Coerce an option to a string. ``strict=True`` raises instead of falling back."""
    if key not in options:
        return default
    value = options[key]
    if value is None:
        return None
    if isinstance(value, (str, Path, date)):
        return str(value)
    if strict:
        raise _bad_option(key, value, "string")
    return default


def opt_str_list(options: dict, key: str, default: list[str], *, strict: bool = False) -> list[str]:
    """Coerce an option to a list of strings (a comma-separated string is accepted)."""
    if key not in options:
        return list(default)
    value = options[key]
    if isinstance(value, str):
        parts = [part.strip() for part in value.split(",")]
        return [part for part in parts if part] or list(default)
    if isinstance(value, (list, tuple)) and all(isinstance(part, str) for part in value):
        return list(value)
    if strict:
        raise _bad_option(key, value, "list of strings (or comma-separated string)")
    return list(default)


def validate_options(options: dict) -> None:
    """Raise ``ValueError`` with a clear message if a known option has a bad type.

    ``run`` uses the permissive ``opt_*`` coercions so malformed values never
    escape as exceptions; the CLI calls this first so a bad config exits 2 with
    an actionable message instead of silently using a default.
    """
    specs: list[tuple[str, Any, Any]] = [
        ("staleness_days", opt_int, 7),
        ("rate_tolerance", opt_float, 2.0),
        ("check_sentiment_emotion", opt_bool, True),
        ("as_of", opt_str, None),
        ("profile", opt_bool, False),
        ("results_log", opt_str, None),
        ("id_column", opt_str, None),
        ("conflict_columns", opt_str_list, []),
    ]
    for key, helper, default in specs:
        if key in options and options[key] is not None:
            helper(options, key, default, strict=True)


# ---------------------------------------------------------------------------
# Dataset loading
# ---------------------------------------------------------------------------


def load_rows(path: str | Path) -> list[dict]:
    """Load rows from a CSV, JSON, or JSONL file.

    CSV needs a header row. JSON must be a list of objects (or an object with
    a ``rows`` or ``data`` list). Each JSONL line must be one object.
    """
    p = Path(path)
    text = p.read_text(encoding="utf-8-sig")
    suffix = p.suffix.lower()

    if suffix in {".jsonl", ".ndjson"}:
        rows: list[dict] = []
        for lineno, line in enumerate(text.splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{p}:{lineno}: invalid JSON line: {exc.msg}") from exc
            if isinstance(obj, dict):
                rows.append(obj)
            else:
                raise ValueError(f"{p}:{lineno}: each JSONL line must be a JSON object")
        return rows

    if suffix == ".json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{p}: invalid JSON: {exc.msg}") from exc
        if isinstance(data, dict):
            data = data.get("rows", data.get("data"))
        if not isinstance(data, list):
            raise ValueError(f"{p}: expected a JSON list of objects (or an object with 'rows'/'data')")
        return [row for row in data if isinstance(row, dict)]

    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        raise ValueError(f"{p}: CSV file has no header row")
    return [dict(row) for row in reader]
