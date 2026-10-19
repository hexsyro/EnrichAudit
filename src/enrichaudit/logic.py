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
    ToolResult,
    as_naive,
    as_str,
    find_id,
    get,
    opt_float,
    opt_int,
    parse_date,
    to_float,
)
from enrichaudit.stats import benford_chi_square, chi_square_sf, modified_zscores

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


class EnrichAuditLogic:
    """Rule engine for EnrichAudit."""

    def run(self, rows: list[dict], options: dict[str, Any]) -> ToolResult:
        staleness_days = max(0, opt_int(options, "staleness_days", 7))
        rate_tolerance = max(0.0, opt_float(options, "rate_tolerance", 2.0))
        check_emotion = bool(options.get("check_sentiment_emotion", True))

        reference = self._reference_date(rows, options)
        findings: list[dict] = []
        warnings: list[str] = []
        rule_counts: dict[str, int] = {}
        checked = 0

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
                continue
            checked += 1
            for finding, rule in zip(row_findings, row_rules, strict=False):
                findings.append(finding)
                rule_counts[rule] = rule_counts.get(rule, 0) + 1

        if checked == 0:
            warnings.append(
                "No rows carried the fields EnrichAudit checks "
                "(engagement metrics, enrichment dates, sentiment/emotion labels)."
            )

        distribution, benford_info = self._distribution_checks(rows, findings)
        rule_counts.update(distribution)

        summary: dict[str, Any] = {
            "checked": checked,
            "flagged": len(findings),
            "rule_counts": rule_counts,
        }
        if benford_info is not None:
            summary["benford"] = benford_info
        if reference:
            summary["reference_date"] = reference.date().isoformat()
        return ToolResult(
            status="success",
            findings=findings,
            warnings=warnings,
            summary=summary,
        )

    def _distribution_checks(
        self, rows: list[dict], findings: list[dict]
    ) -> tuple[dict[str, int], dict | None]:
        """Dataset-level forensics: MAD outliers + Benford screening.

        Both need real sample sizes (8+ / 50+ rows), so small uploads only ever
        see the per-row rules.
        """
        counts: dict[str, int] = {}

        # MAD-based engagement outliers (robust where fixed tolerances mislead
        # on skewed distributions).
        rates: list[tuple[dict, float]] = []
        for row in rows:
            engagements, base, _given = _resolve_engagement_fields(row)
            if base and base > 0 and engagements:
                rates.append((row, sum(engagements) / base * 100.0))
        if len(rates) >= 8:
            scores = modified_zscores([rate for _, rate in rates])
            hits = 0
            for (row, rate), score in zip(rates, scores, strict=False):
                if abs(score) > 3.5:
                    hits += 1
                    if hits <= 5:
                        findings.append(
                            self._finding(
                                "medium",
                                "engagement_rate",
                                f"Engagement outlier: {rate:.2f}% deviates from the dataset "
                                f"distribution (modified z-score {score:+.1f}).",
                                find_id(row),
                                {"computed_rate": round(rate, 2), "modified_z": round(score, 2)},
                            )
                        )
            if hits:
                counts["engagement_outlier"] = hits
                if hits > 5:
                    findings.append(
                        self._finding(
                            "medium",
                            "engagement_rate",
                            f"... and {hits - 5} more engagement outliers.",
                            None,
                            {"overflow": hits - 5},
                        )
                    )

        # Benford's-law screening for fabricated money/count metrics.
        benford_result = None
        for key in ("revenue", "amount", "likes", "impressions", "views", "followers", "clicks"):
            values = [v for v in (to_float(get(r, [key])) for r in rows) if v is not None]
            tested = benford_chi_square(values)
            if tested is None:
                continue
            stat, dof, n = tested
            p_value = chi_square_sf(stat, dof)
            benford_result = {"column": key, "n": n, "chi2": round(stat, 2), "p": round(p_value, 4)}
            if p_value < 0.01:
                findings.append(
                    self._finding(
                        "medium",
                        key,
                        f"First-digit distribution of '{key}' deviates from Benford's law "
                        f"(chi2={stat:.1f}, p={p_value:.4f}, n={n}) — possible fabricated "
                        "or heavily rounded numbers. Investigate, don't conclude.",
                        None,
                        {"check": "benford", **benford_result},
                    )
                )
                counts["benford_deviation"] = 1
            break
        return counts, benford_result

    def _reference_date(self, rows: list[dict], options: dict[str, Any]) -> Any:
        as_of = as_naive(parse_date(options.get("as_of")))
        if as_of:
            return as_of
        latest = None
        for row in rows:
            for key in ("enriched_at", "last_refreshed", "refreshed_at", "fetched_at", "updated_at"):
                parsed = as_naive(parse_date(get(row, [key])))
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
                elif diff > rate_tolerance:
                    findings.append(
                        self._finding(
                            "medium",
                            "engagement_rate",
                            f"Engagement rate {given_rate:.2f}% differs from the computed {computed:.2f}% "
                            f"by more than the {rate_tolerance:g}pt tolerance.",
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
                    f"Computed engagement rate {computed:.2f}% exceeds 100% "
                    f"({total:g} engagements over {base:g}).",
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
                            f"Enrichment field '{date_field}' is {age_days} days stale "
                            f"(threshold {staleness_days}).",
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
    def _finding(
        severity: str, field: str, message: str, record_id: str | None, metadata: dict
    ) -> dict:
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
