"""Rule-by-rule tests for EnrichAudit: every check has a row that must
trigger it, and a clean row that must not."""

from __future__ import annotations

import json

from enrichaudit.logic import EnrichAuditLogic

logic = EnrichAuditLogic()


def rules_of(result) -> dict[str, int]:
    return result.summary.get("rule_counts", {})


def test_negative_rate_is_high():
    rows = [{"id": "1", "engagement_rate": -5, "likes": 10, "impressions": 100}]
    result = logic.run(rows, {})
    assert len(result.findings) == 1
    finding = result.findings[0]
    assert finding["severity"] == "high"
    assert finding["record_id"] == "1"
    assert rules_of(result) == {"negative_rate": 1}


def test_rate_above_100_is_high():
    rows = [{"id": "1", "engagement_rate": 150}]
    result = logic.run(rows, {})
    assert result.findings[0]["severity"] == "high"
    assert "above 100%" in result.findings[0]["message"]
    assert rules_of(result) == {"impossible_rate": 1}


def test_large_recompute_mismatch_is_high():
    rows = [{"id": "1", "likes": 50, "comments": 50, "impressions": 1000, "engagement_rate": 50}]
    result = logic.run(rows, {})
    assert result.findings[0]["severity"] == "high"
    assert result.findings[0]["metadata"]["computed_rate"] == 10.0
    assert rules_of(result) == {"rate_mismatch": 1}


def test_small_recompute_mismatch_is_medium():
    rows = [{"id": "1", "likes": 100, "impressions": 1000, "engagement_rate": 13}]
    result = logic.run(rows, {})
    assert result.findings[0]["severity"] == "medium"
    assert result.findings[0]["metadata"]["delta"] == 3.0


def test_mismatch_within_tolerance_is_clean():
    rows = [{"id": "1", "likes": 100, "impressions": 1000, "engagement_rate": 10.5}]
    result = logic.run(rows, {})
    assert result.findings == []
    assert rules_of(result) == {}


def test_computed_rate_over_100_without_given_rate():
    rows = [{"id": "1", "likes": 200, "impressions": 100}]
    result = logic.run(rows, {})
    assert result.findings[0]["severity"] == "high"
    assert rules_of(result) == {"impossible_rate": 1}


def test_stale_field_low_then_medium():
    rows = [
        {"id": "1", "enriched_at": "2026-01-01", "sentiment": "neutral"},
        {"id": "2", "enriched_at": "2025-12-01", "sentiment": "neutral"},
    ]
    result = logic.run(rows, {"as_of": "2026-01-10", "staleness_days": 7})
    by_id = {f["record_id"]: f for f in result.findings}
    assert by_id["1"]["severity"] == "low"   # 9 days: past threshold, not past 2x
    assert by_id["2"]["severity"] == "medium"  # 40 days: past 2x threshold
    assert rules_of(result) == {"stale_field": 2}


def test_staleness_disabled():
    rows = [{"id": "1", "enriched_at": "2020-01-01"}]
    result = logic.run(rows, {"staleness_days": 0})
    assert result.findings == []


def test_sentiment_emotion_contradiction():
    rows = [{"id": "1", "sentiment": "positive", "emotion": "anger"}]
    result = logic.run(rows, {})
    assert result.findings[0]["severity"] == "medium"
    assert rules_of(result) == {"sentiment_emotion_contradiction": 1}


def test_contradiction_check_can_be_disabled():
    rows = [{"id": "1", "sentiment": "positive", "emotion": "anger"}]
    result = logic.run(rows, {"check_sentiment_emotion": False})
    assert result.findings == []


def test_unknown_labels_are_low_findings():
    rows = [{"id": "1", "sentiment": "meh", "emotion": "meh"}]
    result = logic.run(rows, {})
    fields = sorted(f["field"] for f in result.findings)
    assert fields == ["emotion", "sentiment"]
    assert all(f["severity"] == "low" for f in result.findings)
    assert rules_of(result) == {"field_consistency": 2}


def test_contradiction_not_flagged_for_unknown_emotion():
    # An unrecognized emotion can't contradict anything; only the label warning fires.
    rows = [{"id": "1", "sentiment": "positive", "emotion": "meh"}]
    result = logic.run(rows, {})
    assert rules_of(result) == {"field_consistency": 1}


def test_clean_rows_produce_no_findings():
    rows = [
        {
            "id": "1",
            "likes": 100,
            "comments": 50,
            "impressions": 1000,
            "engagement_rate": 15,
            "sentiment": "positive",
            "emotion": "joy",
            "enriched_at": "2026-10-01",
        }
    ]
    result = logic.run(rows, {"as_of": "2026-10-02"})
    assert result.findings == []
    assert result.warnings == []
    assert result.summary["checked"] == 1
    assert result.summary["flagged"] == 0


def test_messy_column_names_are_recognized():
    rows = [{"Post ID": "9", "Likes": "1,200", "Impressions": "10000", "Engagement Rate": "12"}]
    result = logic.run(rows, {})
    # 1200/10000 = 12%, given 12% — consistent, clean.
    assert result.findings == []
    assert result.summary["checked"] == 1


def test_k_suffix_parsed():
    rows = [{"id": "1", "likes": "5k", "impressions": "100k", "engagement_rate": 5}]
    result = logic.run(rows, {})
    assert result.findings == []


def test_mad_engagement_outlier():
    rows = [
        {"id": str(i), "likes": 100, "impressions": 1000} for i in range(7)
    ] + [{"id": "outlier", "likes": 900, "impressions": 1000}]
    result = logic.run(rows, {})
    outlier_findings = [
        f for f in result.findings if f["metadata"].get("modified_z") is not None
    ]
    assert len(outlier_findings) == 1
    assert outlier_findings[0]["record_id"] == "outlier"
    assert outlier_findings[0]["severity"] == "medium"
    assert rules_of(result)["engagement_outlier"] == 1


def test_no_mad_outlier_without_spread():
    rows = [{"id": str(i), "likes": 100, "impressions": 1000} for i in range(8)]
    result = logic.run(rows, {})
    assert "engagement_outlier" not in rules_of(result)


def test_benford_deviation_flagged():
    # 60 values that all begin with digit 9 — a textbook deviation.
    rows = [{"id": str(i), "revenue": 9 * (10 ** (i % 6))} for i in range(60)]
    result = logic.run(rows, {})
    assert rules_of(result).get("benford_deviation") == 1
    benford = result.summary["benford"]
    assert benford["column"] == "revenue"
    assert benford["n"] == 60
    assert benford["p"] < 0.01


def test_benford_not_tested_on_small_samples():
    rows = [{"id": str(i), "revenue": 9 * (10 ** (i % 6))} for i in range(20)]
    result = logic.run(rows, {})
    assert "benford" not in result.summary
    assert "benford_deviation" not in rules_of(result)


def test_rows_without_relevant_fields_are_checked_clean():
    rows = [{"id": "1", "name": "nothing here"}]
    result = logic.run(rows, {})
    assert result.summary["checked"] == 1
    assert result.findings == []
    assert result.warnings == []


def test_empty_dataset_warns():
    result = logic.run([], {})
    assert result.findings == []
    assert result.summary["checked"] == 0
    assert len(result.warnings) == 1
    assert "No rows carried the fields" in result.warnings[0]


def test_reference_date_defaults_to_latest_in_data():
    rows = [
        {"id": "1", "enriched_at": "2026-01-01"},
        {"id": "2", "enriched_at": "2026-03-01"},
    ]
    result = logic.run(rows, {"staleness_days": 500})
    assert result.summary["reference_date"] == "2026-03-01"


def test_row_errors_do_not_crash_the_run():
    rows = [
        {"id": "1", "likes": 100, "impressions": 1000, "engagement_rate": 10},
        {"id": "2", "likes": "not-a-number", "impressions": None, "engagement_rate": {}},
    ]
    result = logic.run(rows, {})
    assert result.summary["checked"] >= 1
    assert result.status == "success"


# --- v0.2.0 -----------------------------------------------------------------


def test_summary_carries_result_schema_and_skip_state():
    result = logic.run([{"id": "1", "sentiment": "neutral"}], {})
    summary = result.summary
    assert summary["result_schema_version"] == "1.1"
    assert summary["skipped"] is False
    assert summary["skip_reason"] is None

    empty = logic.run([], {})
    assert empty.summary["skipped"] is True
    assert empty.summary["skip_reason"] is not None
    assert empty.summary["result_schema_version"] == "1.1"


def test_row_errors_are_counted_and_warned(monkeypatch):
    original = logic._audit_row

    def flaky(row, **kwargs):
        if row.get("id") == "2":
            raise RuntimeError("boom")
        return original(row, **kwargs)

    monkeypatch.setattr(logic, "_audit_row", flaky)
    rows = [{"id": "1", "engagement_rate": -1}, {"id": "2", "engagement_rate": -1}]
    result = logic.run(rows, {})
    assert result.status == "success"
    assert result.summary["row_errors"] == 1
    assert result.summary["checked"] == 1
    assert any("1 row(s) could not be audited" in w and "2" in w for w in result.warnings)


def test_all_row_errors_are_marked_skipped(monkeypatch):
    def broken(row, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(logic, "_audit_row", broken)
    result = logic.run([{"id": "1"}], {})
    assert result.summary["skipped"] is True
    assert result.summary["row_errors"] == 1
    assert "row_errors" in result.summary["skip_reason"]


def test_benford_tests_multiple_columns_with_warning():
    rows = []
    for i in range(60):
        value = 9 * (10 ** (i % 6))
        rows.append({"id": str(i), "revenue": value, "amount": value})
    result = logic.run(rows, {})
    benford_all = result.summary["benford_all"]
    assert [entry["column"] for entry in benford_all] == ["revenue", "amount"]
    assert result.summary["benford"] == benford_all[0]
    assert rules_of(result)["benford_deviation"] == 2
    assert any("Multiple comparisons" in warning for warning in result.warnings)


def test_benford_stops_at_five_columns():
    columns = ("revenue", "amount", "likes", "impressions", "views", "followers", "clicks")
    rows = [{column: 9 * (10 ** (i % 6)) for column in columns} for i in range(60)]
    result = logic.run(rows, {})
    assert [entry["column"] for entry in result.summary["benford_all"]] == list(columns[:5])


def test_rate_tolerance_medium_tier_when_tolerance_exceeds_ten():
    # Computed 13%, stated 1%: 12pt gap. With a 15pt tolerance this is still
    # medium (10 < 12 <= 15), not silently clean and not high.
    medium = logic.run([{"id": "1", "likes": 1300, "impressions": 10000, "engagement_rate": 1}],
                       {"rate_tolerance": 15})
    assert medium.findings[0]["severity"] == "medium"
    assert medium.findings[0]["metadata"]["delta"] == 12.0

    high = logic.run([{"id": "1", "likes": 1700, "impressions": 10000, "engagement_rate": 1}],
                     {"rate_tolerance": 15})
    assert high.findings[0]["severity"] == "high"


def test_flagged_counts_findings_but_flagged_rows_counts_records():
    rows = [{"id": "1", "engagement_rate": 150, "sentiment": "positive", "emotion": "anger"}]
    rows += [{"id": str(i), "revenue": 9 * (10 ** (i % 6))} for i in range(60)]
    result = logic.run(rows, {})
    # Two per-row findings for id 1, plus one dataset-level Benford finding.
    assert result.summary["flagged"] == 3
    assert result.summary["flagged_rows"] == 1


def test_conflicting_enrichment_per_id():
    rows = [
        {"id": "1", "sentiment": "positive", "emotion": "joy"},
        {"id": "1", "sentiment": "negative", "emotion": "joy"},
        {"id": "2", "sentiment": "positive", "emotion": "joy"},
        {"id": "2", "sentiment": "positive", "emotion": "anger"},
        {"id": "3", "sentiment": "", "emotion": "sad"},
        {"id": "3", "sentiment": "positive", "emotion": "sad"},
    ]
    result = logic.run(rows, {"check_sentiment_emotion": False})
    assert result.summary["conflict_count"] == 2
    assert rules_of(result) == {"conflicting_enrichment": 2}
    assert [finding["record_id"] for finding in result.findings] == ["1", "2"]
    assert all(finding["severity"] == "medium" for finding in result.findings)


def test_conflict_respects_id_column_and_conflict_columns():
    rows = [
        {"pk": "a", "label": "x", "sentiment": "positive"},
        {"pk": "a", "label": "y", "sentiment": "positive"},
    ]
    without = logic.run(rows, {"check_sentiment_emotion": False})
    assert without.summary["conflict_count"] == 0

    with_options = logic.run(
        rows,
        {"id_column": "pk", "conflict_columns": ["label"], "check_sentiment_emotion": False},
    )
    assert with_options.summary["conflict_count"] == 1
    assert with_options.findings[0]["record_id"] == "a"
    assert with_options.findings[0]["metadata"]["columns"] == ["label"]


def test_conflict_examples_are_capped():
    rows = []
    for i in range(7):
        rows += [{"id": str(i), "label": "a"}, {"id": str(i), "label": "b"}]
    result = logic.run(rows, {})
    assert result.summary["conflict_count"] == 7
    conflicts = [finding for finding in result.findings if finding["metadata"]["rule"] == "conflicting_enrichment"]
    assert len(conflicts) == 6  # five examples plus one overflow note
    assert any(finding["metadata"].get("overflow") == 2 for finding in conflicts)


def test_profile_is_exposed_when_enabled():
    rows = [{"id": "1", "likes": 10, "enriched_at": "2026-10-01", "sentiment": "positive"}]
    profile = logic.run(rows, {"profile": True}).summary["profile"]
    assert profile["row_count"] == 1
    assert profile["columns"]["likes"]["type"] == "number"
    assert profile["columns"]["enriched_at"]["type"] == "date"
    assert profile["columns"]["sentiment"]["top_values"] == [{"value": "positive", "count": 1}]
    assert "profile" not in logic.run(rows, {}).summary


def test_results_log_option_appends_one_line_per_finding(tmp_path):
    log = tmp_path / "findings.jsonl"
    result = logic.run([{"id": "1", "engagement_rate": -1}], {"results_log": str(log)})
    lines = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == len(result.findings) == 1
    assert lines[0]["rule"] == "negative_rate"
    assert lines[0]["record_id"] == "1"


def test_malformed_option_values_never_escape_run():
    options = {
        "staleness_days": "nope",
        "rate_tolerance": {},
        "check_sentiment_emotion": "maybe",
        "profile": [],
        "results_log": 123,
        "id_column": {},
        "conflict_columns": 5,
    }
    result = logic.run([{"id": "1", "likes": 10, "impressions": 100}], options)
    assert result.status == "success"
    assert result.summary["result_schema_version"] == "1.1"
