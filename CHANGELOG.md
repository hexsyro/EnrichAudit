# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2026-11-19

### Added

- Generated dataset contracts: `build_contract(rows)`, `--profile FILE`, and
  `summary["profile"]`. Contracts record per-column type, count, null rate,
  distinct count, numeric min/max and p50/p95, categorical top values (max 10),
  and date freshness. `examples/enrichment_contract.json` is generated from
  `examples/sample.csv`.
- Check-results ledger: `--results-log FILE` and the `results_log` option append
  one JSON line per finding (`timestamp`, `severity`, `rule`, `field`,
  `record_id`, `message`). Rules come from `metadata["rule"]` / `check` or fall
  back to the finding's field.
- Golden-record conflict detection: `id_column` (default: `find_id`) and
  `conflict_columns` (default `sentiment,emotion,label,category,source`) produce
  one medium `conflicting_enrichment` finding per conflicting id (with example
  capping) and `summary["conflict_count"]`.
- Shared result contract: `RESULT_SCHEMA_VERSION = "1.1"` and `ToolResult`
  exported from the package root; every `run()` summary carries
  `result_schema_version`, `skipped`, `skip_reason`, `flagged_rows`,
  `row_errors`, and `conflict_count`. JSON output also carries
  `result_schema_version` in the envelope.
- CLI validation of config value types with clear stderr messages and exit 2
  for malformed values (`staleness_days`, `rate_tolerance`,
  `check_sentiment_emotion`, `as_of`, `profile`, `results_log`, `id_column`,
  `conflict_columns`).

### Fixed

- Row-level exceptions no longer vanish into `except Exception: continue`:
  they are counted in `summary["row_errors"]` and warned about with up to five
  example record ids. Dataset-level checks no longer trip over those rows.
- Benford screening tests up to five eligible columns instead of breaking after
  the first. `summary["benford"]` remains the first result for backward
  compatibility, `summary["benford_all"]` holds all of them, and a warning is
  emitted when multiple columns are tested.
- Rate tolerance tiers: with `rate_tolerance > 10`, a gap above
  `min(tolerance, 10)` and up to `max(tolerance, 10)` is now medium rather than
  unreachable; larger gaps remain high.
- `flagged` is documented as a finding count; `flagged_rows` counts distinct
  record ids.
- `to_float` docstring corrected: percent signs are ignored, so rates are
  expressed in percentage points (0-100).

### Inspiration

- [DataKitchen 2026 landscape](https://datakitchen.io/blog/the-2026-open-source-data-quality-and-data-observability-landscape)
  — coverage is not capability, and profiling can generate tests from data.
- [Data quality tooling compared](https://iceberglakehouse.com/posts/data-quality-tooling-compared)
  — `check_results` tables, warn severity, the grain of a check, and rule
  pruning.
- [HN: Get me out of data hell](https://news.ycombinator.com/item?id=42010249)
  — silent failures are what make data pipelines painful.

## [0.1.0] - 2026-10-19

### Added

- Initial release.
- Engagement-rate sanity checks: negative rates, rates above 100%, recompute
  mismatch against raw like/comment/share fields, configurable tolerance.
- Staleness detection on enrichment date fields with a reference date that can
  be pinned (`as_of`) or inferred from the data.
- Sentiment vs. emotion contradiction checks and out-of-enum label warnings.
- Dataset-level forensics: MAD-based engagement outliers (8+ rows) and
  Benford's-law first-digit screening (50+ values).
- CLI with `table`, `json`, `csv`, and `markdown` output, `--fail-on` exit
  codes for CI, and JSON config for thresholds.
- Importable library API: `enrichaudit.run(rows, options)`.
