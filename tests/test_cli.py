"""End-to-end CLI tests: file in, report out, exit codes right."""

from __future__ import annotations

import csv
import json

import pytest

from enrichaudit.cli import main

BAD_CSV = """id,likes,comments,impressions,engagement_rate,sentiment,emotion,enriched_at
1,50,50,1000,50,positive,joy,2026-10-01
2,100,0,1000,10,positive,anger,2026-10-01
3,100,0,1000,10,positive,joy,2026-10-01
"""

CLEAN_CSV = """id,likes,comments,impressions,engagement_rate,sentiment,emotion,enriched_at
1,100,50,1000,15,positive,joy,2026-10-01
"""


def write_csv(tmp_path, content: str):
    path = tmp_path / "data.csv"
    path.write_text(content, encoding="utf-8")
    return path


def test_json_output_envelope(tmp_path, capsys):
    path = write_csv(tmp_path, BAD_CSV)
    rc = main([str(path), "--format", "json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["tool"] == "enrichaudit"
    assert payload["status"] == "success"
    assert payload["summary"]["checked"] == 3
    assert len(payload["findings"]) == 2
    sevs = sorted(f["severity"] for f in payload["findings"])
    assert sevs == ["high", "medium"]


def test_table_output_has_findings(capsys, tmp_path):
    path = write_csv(tmp_path, BAD_CSV)
    assert main([str(path)]) == 0
    out = capsys.readouterr().out
    assert "finding(s)" in out
    assert "engagement_rate" in out
    assert "Rules:" in out


def test_markdown_output(capsys, tmp_path):
    path = write_csv(tmp_path, BAD_CSV)
    assert main([str(path), "--format", "md"]) == 0
    out = capsys.readouterr().out
    assert "# enrichaudit report" in out
    assert "| Severity | Field | Record | Message |" in out


def test_csv_output_parses(capsys, tmp_path):
    path = write_csv(tmp_path, BAD_CSV)
    assert main([str(path), "--format", "csv"]) == 0
    rows = list(csv.DictReader(capsys.readouterr().out.splitlines()))
    assert len(rows) == 2
    assert {r["severity"] for r in rows} == {"high", "medium"}


def test_output_file(tmp_path):
    path = write_csv(tmp_path, BAD_CSV)
    report = tmp_path / "report.json"
    assert main([str(path), "--format", "json", "--output", str(report)]) == 0
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["tool"] == "enrichaudit"


def test_fail_on_high_trips_on_bad_row(tmp_path, capsys):
    path = write_csv(tmp_path, BAD_CSV)
    assert main([str(path), "--fail-on", "high"]) == 1


def test_fail_on_high_passes_on_medium_only(tmp_path, capsys):
    # Row 2's mismatch: given 13 vs computed 10 -> medium, not high.
    content = (
        "id,likes,impressions,engagement_rate\n"
        "1,100,1000,13\n"
    )
    path = write_csv(tmp_path, content)
    assert main([str(path), "--fail-on", "high"]) == 0
    assert main([str(path), "--fail-on", "medium"]) == 1
    assert main([str(path), "--fail-on", "low"]) == 1


def test_fail_on_none_with_findings(tmp_path):
    path = write_csv(tmp_path, BAD_CSV)
    assert main([str(path), "--fail-on", "none"]) == 0


def test_clean_data_default_exit_zero(tmp_path):
    path = write_csv(tmp_path, CLEAN_CSV)
    assert main([str(path), "--fail-on", "high"]) == 0


def test_missing_file_exit_2(capsys):
    assert main(["does-not-exist.csv"]) == 2
    assert "file not found" in capsys.readouterr().err


def test_jsonl_input(tmp_path, capsys):
    path = tmp_path / "data.jsonl"
    path.write_text(
        '{"id": "1", "engagement_rate": -1}\n\n{"id": "2", "likes": 1, "impressions": 100}\n',
        encoding="utf-8",
    )
    assert main([str(path), "--format", "json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["summary"]["checked"] == 2


def test_broken_jsonl_exit_2(tmp_path, capsys):
    path = tmp_path / "data.jsonl"
    path.write_text("{not json}\n", encoding="utf-8")
    assert main([str(path)]) == 2
    assert "invalid JSON line" in capsys.readouterr().err


def test_config_file_wires_options(tmp_path, capsys):
    path = tmp_path / "data.csv"
    path.write_text(
        "id,sentiment,enriched_at\n1,positive,2026-01-01\n",
        encoding="utf-8",
    )
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"as_of": "2026-01-20", "staleness_days": 10}), encoding="utf-8")

    # Without config the reference date is the data's own latest date -> no staleness.
    assert main([str(path), "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["findings"] == []

    # With config: 19 days old vs a 10-day threshold -> stale.
    assert main([str(path), "--config", str(config), "--format", "json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["summary"]["rule_counts"] == {"stale_field": 1}


def test_cli_flag_overrides_config(tmp_path, capsys):
    path = tmp_path / "data.csv"
    path.write_text("id,sentiment,enriched_at\n1,positive,2026-01-01\n", encoding="utf-8")
    config = tmp_path / "config.json"
    config.write_text(json.dumps({"staleness_days": 100}), encoding="utf-8")
    assert main([str(path), "--config", str(config), "--staleness-days", "1", "--as-of", "2026-02-01",
                 "--format", "json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["summary"]["rule_counts"] == {"stale_field": 1}


def test_no_sentiment_emotion_flag(tmp_path, capsys):
    path = tmp_path / "data.csv"
    path.write_text("id,sentiment,emotion\n1,positive,anger\n", encoding="utf-8")
    assert main([str(path), "--no-sentiment-emotion", "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out)["findings"] == []


def test_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert "enrichaudit 0.2.0" in capsys.readouterr().out


def test_json_envelope_has_result_schema_version(tmp_path, capsys):
    path = write_csv(tmp_path, CLEAN_CSV)
    assert main([str(path), "--format", "json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["result_schema_version"] == "1.1"
    assert payload["summary"]["result_schema_version"] == "1.1"
    assert payload["summary"]["skipped"] is False
    assert payload["summary"]["skip_reason"] is None


@pytest.mark.parametrize(
    ("config", "key"),
    [
        ({"staleness_days": "banana"}, "staleness_days"),
        ({"rate_tolerance": {}}, "rate_tolerance"),
        ({"check_sentiment_emotion": "maybe"}, "check_sentiment_emotion"),
        ({"as_of": ["2026-01-01"]}, "as_of"),
        ({"profile": "yes please"}, "profile"),
        ({"results_log": 42}, "results_log"),
        ({"id_column": {"name": "id"}}, "id_column"),
        ({"conflict_columns": [1, 2]}, "conflict_columns"),
    ],
)
def test_invalid_config_value_types_exit_2(tmp_path, capsys, config, key):
    path = write_csv(tmp_path, CLEAN_CSV)
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps(config), encoding="utf-8")
    assert main([str(path), "--config", str(cfg)]) == 2
    err = capsys.readouterr().err
    assert "invalid value for option" in err
    assert key in err


def test_profile_flag_writes_contract_and_keeps_checks(tmp_path, capsys):
    path = write_csv(tmp_path, BAD_CSV)
    profile_path = tmp_path / "contract.json"
    assert main([str(path), "--profile", str(profile_path), "--format", "json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    contract = json.loads(profile_path.read_text(encoding="utf-8"))
    assert contract == payload["summary"]["profile"]
    assert contract["row_count"] == 3
    assert "engagement_rate" in contract["columns"]
    # Normal checks still ran alongside profiling.
    assert payload["summary"]["checked"] == 3
    assert len(payload["findings"]) == 2


def test_results_log_flag_appends_json_lines(tmp_path, capsys):
    path = write_csv(tmp_path, BAD_CSV)
    log_path = tmp_path / "findings.jsonl"
    assert main([str(path), "--results-log", str(log_path), "--format", "json"]) == 0
    capsys.readouterr()
    lines = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 2
    assert all(set(line) == {"timestamp", "severity", "rule", "field", "record_id", "message"} for line in lines)
    assert {line["rule"] for line in lines} == {"rate_mismatch", "sentiment_emotion_contradiction"}
    assert all(line["timestamp"].endswith("Z") for line in lines)


def test_conflict_columns_flag(tmp_path, capsys):
    path = write_csv(tmp_path, "id,category,source\n1,news,alpha\n1,blog,alpha\n")
    assert main([str(path), "--conflict-columns", "category,source", "--format", "json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["summary"]["conflict_count"] == 1
    assert [f["severity"] for f in payload["findings"]] == ["medium"]

