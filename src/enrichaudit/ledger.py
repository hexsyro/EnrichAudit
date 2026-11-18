"""Append-only check-results ledger (one JSON object per finding per line)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def derive_rule(finding: dict) -> str:
    """Find the rule name for a finding.

    Rules are carried in ``metadata['rule']`` by the engine; older or
    third-party findings fall back to their field name (or ``unknown``).
    """
    metadata = finding.get("metadata") or {}
    if isinstance(metadata, dict):
        for key in ("rule", "check"):
            if metadata.get(key):
                return str(metadata[key])
    field = finding.get("field")
    return str(field) if field else "unknown"


def append_results_log(findings: list[dict], path: str | Path) -> int:
    """Append one JSON line per finding and return the number of lines written."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    with target.open("a", encoding="utf-8") as stream:
        for finding in findings:
            line: dict[str, Any] = {
                "timestamp": timestamp,
                "severity": finding.get("severity", "low"),
                "rule": derive_rule(finding),
                "field": finding.get("field", ""),
                "record_id": finding.get("record_id"),
                "message": finding.get("message", ""),
            }
            stream.write(json.dumps(line, ensure_ascii=False, default=str) + "\n")
    return len(findings)
