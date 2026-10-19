# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
