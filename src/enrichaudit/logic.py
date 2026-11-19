"""EnrichAudit — automation of the manual QA data teams already do on social/content pipelines.

Catches:
  - engagement-rate sanity failures (impossible or miscalculated rates)
  - stale enrichment fields that haven't refreshed when they should have
  - sentiment vs. emotion label contradictions
  - field-consistency anomalies (out-of-enum values)
  - distribution anomalies: MAD-based engagement outliers and Benford's-law
    screening for fabricated money/count metrics (both need real sample sizes,
    so single-row uploads are unaffected)
"""

from __future__ import annotations

from typing import Any

from enrichaudit.common import (
    RESULT_SCHEMA_VERSION,
    ToolResult,
    as_naive,
    as_str,
    find_id,
    get,
    opt_bool,
    opt_float,
    opt_int,
    opt_str,
    opt_str_list,
    parse_date,
    to_float,
)
from enrichaudit.ledger import append_results_log
from enrichaudit.profile import build_contract
from enrichaudit.stats import benford_chi_square, chi_square_sf, modified_zscores

DEFAULT_CONFLICT_COLUMNS = ["sentiment", "emotion", "label", "category", "source"]
MAX_EXAMPLES = 5
MAX_BENFORD_COLUMNS = 5

POSITIVE_LABELS = {"positive", "pos", "1", "1.0", "liked", "love"}
NEGATIVE_LABELS = {"negative", "neg", "-1", "-1.0", "hated", "dislike"}
SENTIMENT_ALLOWED = POSITIVE_LABELS | NEGATIVE_LABELS | {"neutral", "mixed", "0", "0.0", "unknown"}

EMOTION_SENTIMENT: dict[str, str] = {
    "joy": "positive",
    "happiness": "positive",
    "happy": "positive",
    "love": "positive",
    "pride": "positive",
    "excitement": "positive",
    "excited": "positive",
    "amusement": "positive",
    "amused": "positive",
    "awe": "positive",
    "contentment": "positive",
    "content": "positive",
    "gratitude": "positive",
    "grateful": "positive",
    "anger": "negative",
    "angry": "negative",
    "sadness": "negative",
    "sad": "negative",
    "disgust": "negative",
    "disgusted": "negative",
    "fear": "negative",
    "afraid": "negative",
    "frustration": "negative",
    "frustrated": "negative",
    "disappointment": "negative",
    "disappointed": "negative",
    "grief": "negative",
    "shame": "negative",
    "contempt": "negative",
    "anxiety": "negative",
    "anxious": "negative",
    "boredom": "negative",
    "bored": "negative",
}
EMOTION_ALLOWED = set(EMOTION_SENTIMENT) | {"neutral", "surprise", "anticipation", "unknown", ""}


def _sentiment_polarity(label: str | None) -> str | None:
    if label is None:
        return None
    lowered = label.strip().lower()
    if lowered in POSITIVE_LABELS:
        return "positive"
    if lowered in NEGATIVE_LABELS:
        return "negative"
    if lowered in {"neutral", "mixed", "0", "0.0"}:
        return "neutral"
    return None


def _resolve_engagement_fields(row: dict) -> tuple[list[float], float | None, float | None]:
    """Return (raw engagements, base, given engagement rate) for a row."""
    engagements = []
    for key in ("likes", "comments", "shares", "reposts", "reactions", "retweets", "clicks"):
        value = to_float(get(row, [key]))
        if value is not None:
            engagements.append(value)
    base = to_float(get(row, ["impressions", "reach", "views", "followers", "author_followers"]))
    given_rate = to_float(get(row, ["engagement_rate", "er", "engagement_ratio", "engagement"]))
    return engagements, base, given_rate


def _row_id(row: dict, id_column: str | None = None) -> str | None:
    """Best-effort id, optionally from an explicit column, never raising."""
    try:
        value = get(row, [id_column]) if id_column else find_id(row)
        return as_str(value) or None
    except Exception:
        return None


class EnrichAuditLogic:
    """Rule engine for EnrichAudit."""

    def run(self, rows: list[dict], options: dict[str, Any]) -> ToolResult:
        staleness_days = max(0, opt_int(options, "staleness_days", 7))
        rate_tolerance = max(0.0, opt_float(options, "rate_tolerance", 2.0))
        check_emotion = opt_bool(options, "check_sentiment_emotion", True)
        include_profile = opt_bool(options, "profile", False)
        results_log = opt_str(options, "results_log", None)
        id_column = opt_str(options, "id_column", None)
        conflict_columns = opt_str_list(options, "conflict_columns", DEFAULT_CONFLICT_COLUMNS)

        reference = self._reference_date(rows, options)
        findings: list[dict] = []
        warnings: list[str] = []
        rule_counts: dict[str, int] = {}
        checked = 0
        row_error_ids: list[str] = []
        checked_rows: list[dict] = []

        for row in rows:
            try:
                row_findings, row_rules = self._audit_row(
                    row,
                    staleness_days=staleness_days,
                    rate_tolerance=rate_tolerance,
                    check_emotion=check_emotion,
                    reference=reference,
                )
            except Exception:
                row_error_ids.append(_row_id(row) or "<unknown>")
                continue
            checked += 1
            checked_rows.append(row)
            for finding, rule in zip(row_findings, row_rules, strict=False):
                finding.setdefault("metadata", {})["rule"] = rule
                findings.append(finding)
                rule_counts[rule] = rule_counts.get(rule, 0) + 1

        distribution, benford_all = self._distribution_checks(checked_rows, findings, warnings)
        rule_counts.update(distribution)

        conflict_count = self._conflict_checks(rows, findings, id_column, conflict_columns)
        if conflict_count:
            rule_counts["conflicting_enrichment"] = conflict_count

        if row_error_ids:
            examples = ", ".join(row_error_ids[:MAX_EXAMPLES])
            warnings.append(
                f"{len(row_error_ids)} row(s) could not be audited (examples: {examples}). "
                "Those rows were skipped; this is a data-shape problem, not a clean result."
            )

        skipped = checked == 0
        skip_reason: str | None = None
        if skipped:
            skip_reason = (
                "No rows could be audited; see row_errors."
                if row_error_ids
                else "No rows carried the fields EnrichAudit checks "
                "(engagement metrics, enrichment dates, sentiment/emotion labels)."
            )
            warnings.append(skip_reason)

        summary: dict[str, Any] = {
            "result_schema_version": RESULT_SCHEMA_VERSION,
            "skipped": skipped,
            "skip_reason": skip_reason,
            "checked": checked,
            # `flagged` counts findings (historical meaning); `flagged_rows`
            # counts distinct record ids, i.e. the human notion of bad rows.
            "flagged": len(findings),
            "flagged_rows": len({f["record_id"] for f in findings if f.get("record_id")}),
            "row_errors": len(row_error_ids),
            "conflict_count": conflict_count,
            "rule_counts": rule_counts,
        }
        if benford_all:
            summary["benford"] = benford_all[0]  # backward compatible
            summary["benford_all"] = benford_all
        if reference:
            summary["reference_date"] = reference.date().isoformat()
        if include_profile:
            try:
                summary["profile"] = build_contract(rows)
            except Exception as exc:  # profiling must never sink an audit
                warnings.append(f"Could not build profile: {exc}")
        if results_log:
            try:
                append_results_log(findings, results_log)
            except (OSError, TypeError, ValueError) as exc:
                warnings.append(f"Could not write results log '{results_log}': {exc}")

        return ToolResult(status="success", findings=findings, warnings=warnings, summary=summary)

    def _distribution_checks(
        self, rows: list[dict], findings: list[dict], warnings: list[str]
    ) -> tuple[dict[str, int], list[dict]]:
        """Dataset-level forensics: MAD outliers + Benford screening.

        Both need real sample sizes (8+ / 50+ rows), so small uploads only ever
        see the per-row rules. Benford tests up to five eligible columns; the
        first result stays at ``summary['benford']`` for backward compat.
        """
        counts: dict[str, int] = {}

        # MAD-based engagement outliers (robust where fixed tolerances mislead
        # on skewed distributions).
        rates: list[tuple[dict, float]] = []
        for row in rows:
            try:
                engagements, base, _given = _resolve_engagement_fields(row)
            except Exception:
                continue
            if base and base > 0 and engagements:
                rates.append((row, sum(engagements) / base * 100.0))
        if len(rates) >= 8:
            scores = modified_zscores([rate for _, rate in rates])
            hits = 0
            for (row, rate), score in zip(rates, scores, strict=False):
                if abs(score) > 3.5:
                    hits += 1
                    if hits <= MAX_EXAMPLES:
                        findings.append(
                            self._finding(
                                "medium",
                                "engagement_rate",
                                f"Engagement outlier: {rate:.2f}% deviates from the dataset "
                                f"distribution (modified z-score {score:+.1f}).",
                                _row_id(row),
                                {
                                    "rule": "engagement_outlier",
                                    "computed_rate": round(rate, 2),
                                    "modified_z": round(score, 2),
                                },
                            )
                        )
            if hits:
                counts["engagement_outlier"] = hits
                if hits > MAX_EXAMPLES:
                    findings.append(
                        self._finding(
                            "medium",
                            "engagement_rate",
                            f"... and {hits - MAX_EXAMPLES} more engagement outliers.",
                            None,
                            {"rule": "engagement_outlier", "overflow": hits - MAX_EXAMPLES},
                        )
                    )

        # Benford's-law screening for fabricated money/count metrics.
        benford_all: list[dict] = []
        for key in ("revenue", "amount", "likes", "impressions", "views", "followers", "clicks"):
            try:
                values = [v for v in (to_float(get(r, [key])) for r in rows) if v is not None]
            except Exception:
                continue
            tested = benford_chi_square(values)
            if tested is None:
                continue
            stat, dof, n = tested
            p_value = chi_square_sf(stat, dof)
            result = {"column": key, "n": n, "chi2": round(stat, 2), "p": round(p_value, 4)}
            benford_all.append(result)
            if p_value < 0.01:
                findings.append(
                    self._finding(
                        "medium",
                        key,
                        f"First-digit distribution of '{key}' deviates from Benford's law "
                        f"(chi2={stat:.1f}, p={p_value:.4f}, n={n}) — possible fabricated "
                        "or heavily rounded numbers. Investigate, don't conclude.",
                        None,
                        {"rule": "benford_deviation", "check": "benford", **result},
                    )
                )
                counts["benford_deviation"] = counts.get("benford_deviation", 0) + 1
            if len(benford_all) >= MAX_BENFORD_COLUMNS:
                break
        if len(benford_all) > 1:
            warnings.append(
                f"Benford screening tested {len(benford_all)} columns. Multiple comparisons "
                "raise the false-positive rate; treat each deviation as a lead to investigate, "
                "not a conclusion."
            )
        return counts, benford_all

    def _conflict_checks(
        self,
        rows: list[dict],
        findings: list[dict],
        id_column: str | None,
        conflict_columns: list[str],
    ) -> int:
        """One medium finding per id whose enrichment labels disagree."""
        grouped: dict[str, dict[str, set[str]]] = {}
        for row in rows:
            record_id = _row_id(row, id_column)
            if not record_id:
                continue
            for column in conflict_columns:
                try:
                    text = as_str(get(row, [column])).strip()
                except Exception:
                    continue
                if text:
                    grouped.setdefault(record_id, {}).setdefault(column, set()).add(text.lower())

        conflicting: list[tuple[str, dict[str, set[str]]]] = []
        for record_id, values in grouped.items():
            differing = {column: seen for column, seen in values.items() if len(seen) > 1}
            if differing:
                conflicting.append((record_id, differing))

        for record_id, differing in conflicting[:MAX_EXAMPLES]:
            fields = ", ".join(sorted(differing))
            findings.append(
                self._finding(
                    "medium",
                    "enrichment",
                    f"Conflicting enrichment for record {record_id}: differing values in {fields}.",
                    record_id,
                    {"rule": "conflicting_enrichment", "columns": sorted(differing)},
                )
            )
        if len(conflicting) > MAX_EXAMPLES:
            overflow = len(conflicting) - MAX_EXAMPLES
            findings.append(
                self._finding(
                    "medium",
                    "enrichment",
                    f"... and {overflow} more conflicting enrichment records.",
                    None,
                    {"rule": "conflicting_enrichment", "overflow": overflow},
                )
            )
        return len(conflicting)

    def _reference_date(self, rows: list[dict], options: dict[str, Any]) -> Any:
        as_of = as_naive(parse_date(options.get("as_of")))
        if as_of:
            return as_of
        latest = None
        for row in rows:
            for key in ("enriched_at", "last_refreshed", "refreshed_at", "fetched_at", "updated_at"):
                try:
                    raw = get(row, [key])
                except Exception:
                    continue
                parsed = as_naive(parse_date(raw))
                if parsed and (latest is None or parsed > latest):
                    latest = parsed
        return latest

    def _audit_row(
        self,
        row: dict,
        *,
        staleness_days: int,
        rate_tolerance: float,
        check_emotion: bool,
        reference: Any,
    ) -> tuple[list[dict], list[str]]:
        findings: list[dict] = []
        rules: list[str] = []
        record_id = find_id(row)

        # --- engagement-rate sanity -------------------------------------------
        engagements, base, given_rate = _resolve_engagement_fields(row)
        total = sum(engagements)
        computed = (total / base * 100.0) if base and base > 0 else None

        if given_rate is not None:
            if given_rate < 0:
                findings.append(
                    self._finding(
                        "high",
                        "engagement_rate",
                        "Engagement rate is negative, which is impossible.",
                        record_id,
                        {"given_rate": round(given_rate, 4)},
                    )
                )
                rules.append("negative_rate")
            elif given_rate > 100.0:
                findings.append(
                    self._finding(
                        "high",
                        "engagement_rate",
                        f"Engagement rate {given_rate:.2f}% is above 100%, which is impossible.",
                        record_id,
                        {"given_rate": round(given_rate, 2)},
                    )
                )
                rules.append("impossible_rate")
            elif computed is not None:
                diff = abs(computed - given_rate)
                meta = {
                    "given_rate": round(given_rate, 2),
                    "computed_rate": round(computed, 2),
                    "delta": round(diff, 2),
                }
                medium_tier = min(rate_tolerance, 10.0)
                if diff > max(rate_tolerance, 10.0):
                    findings.append(
                        self._finding(
                            "high",
                            "engagement_rate",
                            f"Engagement rate is {given_rate:.2f}% but recomputing from raw fields "
                            f"({total:g} engagements over {base:g}) gives {computed:.2f}%.",
                            record_id,
                            meta,
                        )
                    )
                    rules.append("rate_mismatch")
                elif diff > medium_tier:
                    findings.append(
                        self._finding(
                            "medium",
                            "engagement_rate",
                            f"Engagement rate {given_rate:.2f}% differs from the computed {computed:.2f}% "
                            f"by more than the {medium_tier:g}pt medium tier.",
                            record_id,
                            meta,
                        )
                    )
                    rules.append("rate_mismatch")
        elif computed is not None and computed > 100.0:
            findings.append(
                self._finding(
                    "high",
                    "engagement_rate",
                    f"Computed engagement rate {computed:.2f}% exceeds 100% ({total:g} engagements over {base:g}).",
                    record_id,
                    {"computed_rate": round(computed, 2)},
                )
            )
            rules.append("impossible_rate")

        # --- staleness ----------------------------------------------------------
        if reference and staleness_days > 0:
            date_field, parsed = self._row_enrichment_date(row)
            if parsed:
                age_days = (reference - parsed).days
                if age_days > staleness_days:
                    severity = "medium" if age_days > staleness_days * 2 else "low"
                    findings.append(
                        self._finding(
                            severity,
                            date_field,
                            f"Enrichment field '{date_field}' is {age_days} days stale (threshold {staleness_days}).",
                            record_id,
                            {"age_days": age_days, "threshold_days": staleness_days},
                        )
                    )
                    rules.append("stale_field")

        # --- sentiment / emotion contradiction ----------------------------------
        sentiment = get(row, ["sentiment", "sentiment_label", "sentiment_polarity", "polarity"])
        emotion = get(row, ["emotion", "emotion_label", "primary_emotion"])
        sentiment_text = as_str(sentiment).lower()
        emotion_text = as_str(emotion).lower()

        if sentiment_text and sentiment_text not in SENTIMENT_ALLOWED:
            findings.append(
                self._finding(
                    "low",
                    "sentiment",
                    f"Sentiment value '{sentiment_text}' is not a recognized sentiment label.",
                    record_id,
                    {"value": sentiment_text},
                )
            )
            rules.append("field_consistency")

        if emotion_text and emotion_text not in EMOTION_ALLOWED:
            findings.append(
                self._finding(
                    "low",
                    "emotion",
                    f"Emotion value '{emotion_text}' is not a recognized emotion label.",
                    record_id,
                    {"value": emotion_text},
                )
            )
            rules.append("field_consistency")

        if check_emotion and sentiment_text and emotion_text:
            sentiment_polarity = _sentiment_polarity(sentiment_text)
            emotion_polarity = EMOTION_SENTIMENT.get(emotion_text)
            if (
                sentiment_polarity in ("positive", "negative")
                and emotion_polarity is not None
                and sentiment_polarity != emotion_polarity
            ):
                findings.append(
                    self._finding(
                        "medium",
                        "sentiment",
                        f"Sentiment '{sentiment_text}' contradicts emotion '{emotion_text}' "
                        f"({sentiment_polarity} vs {emotion_polarity}).",
                        record_id,
                        {"sentiment": sentiment_text, "emotion": emotion_text},
                    )
                )
                rules.append("sentiment_emotion_contradiction")

        return findings, rules

    def _row_enrichment_date(self, row: dict) -> tuple[str, Any]:
        for key in (
            "enriched_at",
            "last_refreshed",
            "refreshed_at",
            "fetched_at",
            "updated_at",
            "collected_at",
        ):
            parsed = as_naive(parse_date(get(row, [key])))
            if parsed:
                return key, parsed
        return "enriched_at", None

    @staticmethod
    def _finding(severity: str, field: str, message: str, record_id: str | None, metadata: dict) -> dict:
        return {
            "severity": severity,
            "field": field,
            "message": message,
            "record_id": record_id,
            "metadata": metadata,
        }


def run(rows: list[dict], options: dict[str, Any] | None = None) -> ToolResult:
    """Library entry point: run the audit on ``rows``."""
    return EnrichAuditLogic().run(rows, options or {})
