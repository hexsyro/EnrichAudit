# EnrichAudit

Find the bugs your enrichment pipeline keeps shipping.

## Why this exists

Every team that enriches social or content data ends up doing the same QA by
hand. An engagement rate that doesn't add up. An enrichment field that hasn't
refreshed in three weeks. A row where the sentiment says "positive" and the
emotion says "anger." Someone opens the spreadsheet, squints, and checks a
sample. The sample passes. The dashboard ships wrong numbers.

EnrichAudit is the rule set that manual review keeps rediscovering, written
down once and runnable on every export. It reads your CSV, JSON, or JSONL,
applies the checks, and tells you what's wrong, how many rows it touched, and
how bad it is.

## What it does

- **Engagement-rate sanity.** Recomputes rates from raw likes, comments,
  shares, and impressions. Flags negative rates, rates above 100%, and
  stated-versus-recomputed mismatches beyond your tolerance.
- **Staleness.** Flags enrichment date fields older than a threshold, with a
  reference date you can pin or let the data pick.
- **Field consistency.** Sentiment-vs-emotion contradictions, plus
  out-of-vocabulary labels.
- **Dataset forensics.** MAD-based engagement outliers (8+ rows) and
  Benford's-law first-digit screening for fabricated numbers (50+ values).
  Both stay quiet on small samples instead of guessing.

Every finding carries a severity, the affected record id, and the numbers
that produced the verdict. Nothing is scored by a model. Everything is
reproducible.

## What it is not

It is not a data-quality platform. There is no dashboard, no account, no
scheduled cloud scan, no API key. It's a command you run and a report you
get. If you need someone else to watch your pipeline while you sleep, hire
that.

## Install

Python 3.10+, no runtime dependencies.

```bash
git clone https://github.com/hexsyro/EnrichAudit.git
cd EnrichAudit
python -m pip install .
```

For development:

```bash
python -m pip install -e ".[dev]"
python -m pytest
```

## Usage

```bash
enrichaudit examples/sample.csv
```

```
enrichaudit 0.2.0 — 7 finding(s), 10 row(s) checked

  [medium] 3              sentiment
           Sentiment 'positive' contradicts emotion 'anger' (positive vs negative).

  [high  ] 5              engagement_rate
           Engagement rate 306.00% is above 100%, which is impossible.

  [medium] 6              enriched_at
           Enrichment field 'enriched_at' is 41 days stale (threshold 7).

  [medium] 6              sentiment
           Sentiment 'negative' contradicts emotion 'joy' (negative vs positive).

  [low   ] 7              sentiment
           Sentiment value 'meh' is not a recognized sentiment label.

  [medium] 2              engagement_rate
           Engagement outlier: 8.50% deviates from the dataset distribution (modified z-score
           +4.4).

  [medium] 5              engagement_rate
           Engagement outlier: 30.60% deviates from the dataset distribution (modified z-score
           +24.8).

Rules: engagement_outlier=2, field_consistency=1, impossible_rate=1, sentiment_emotion_contradiction=2, stale_field=1
```

Point it at a file. CSV needs a header row. JSON must be a list of objects;
JSONL is one object per line. Column names are matched loosely — `Post ID`,
`post-id`, and `post_id` are all the same column.

## Options

| Flag | What it does |
| --- | --- |
| `--staleness-days N` | Flag enrichment fields older than N days (default 7) |
| `--rate-tolerance PT` | Allowed gap in percentage points between given and recomputed rate (default 2.0) |
| `--as-of DATE` | Reference date for staleness; defaults to the latest date in the data |
| `--no-sentiment-emotion` | Skip the sentiment-vs-emotion contradiction check |
| `--profile FILE` | Write a generated JSON column contract to FILE; normal checks still run (also sets `options["profile"]`) |
| `--results-log FILE` | Append one JSON line per finding to FILE: `timestamp`, `severity`, `rule`, `field`, `record_id`, `message` |
| `--id-column COLUMN` | Column to use as the record id for conflict detection (default: best-effort `find_id`) |
| `--conflict-columns COLUMNS` | Comma-separated enrichment columns to compare for conflicts (default `sentiment,emotion,label,category,source`) |
| `--config FILE` | JSON file with any of the same keys (`staleness_days`, `rate_tolerance`, `check_sentiment_emotion`, `as_of`, `profile`, `results_log`, `id_column`, `conflict_columns`); explicit flags win. Invalid value types exit 2 with a clear error |
| `--format {table,json,csv,md}` | Output format (default `table`) |
| `--output FILE` | Write the report to a file instead of stdout |
| `--fail-on {none,low,medium,high}` | Exit 1 if any finding is at or above this severity |
| `--version` | Print version and exit |

## Output formats

`table` is for humans at a terminal. `csv` is one row per finding, for
spreadsheets. `md` is a markdown report, for pull requests and tickets.

`json` is the machine contract — and the thing other tools in this family
read:

```json
{
  "tool": "enrichaudit",
  "version": "0.2.0",
  "result_schema_version": "1.1",
  "status": "success",
  "findings": [
    {
      "severity": "high",
      "field": "engagement_rate",
      "message": "Engagement rate 306.00% is above 100%, which is impossible.",
      "record_id": "5",
      "metadata": {"given_rate": 306.0, "rule": "impossible_rate"}
    }
  ],
  "warnings": [],
  "summary": {
    "result_schema_version": "1.1",
    "skipped": false,
    "skip_reason": null,
    "checked": 10,
    "flagged": 1,
    "flagged_rows": 1,
    "row_errors": 0,
    "conflict_count": 0,
    "rule_counts": {"impossible_rate": 1}
  }
}
```

`result_schema_version` travels in both the envelope and the summary so
downstream tools can detect a shape change. `skipped` / `skip_reason` say
whether the audit actually had anything to check; `status` stays backward
compatible. `flagged` is the number of **findings**, while `flagged_rows` is
the number of **distinct record ids** with at least one finding — one bad row
can produce several findings. `row_errors` counts rows whose audit failed;
those rows are skipped and reported in `warnings` with up to five example ids,
never silently dropped.

Every tool in the family emits this shape, so `enrichaudit data.csv -f json >
findings.json` can be fed straight to WhyChanged or ReadyPack.

## Using it in CI

```bash
enrichaudit export.csv --fail-on high || exit 1
```

Exit codes are boring on purpose: `0` ran fine, `1` findings tripped your
`--fail-on` gate, `2` the input was broken. Wire `1` into a failing build and
`2` into an alert — a missing file and a bad row are different problems.

## Library use

```python
from enrichaudit import run

result = run(rows, {"staleness_days": 14, "as_of": "2026-10-01"})
for finding in result.findings:
    print(finding["severity"], finding["record_id"], finding["message"])
```

`run` takes a list of dicts — the same rows a CSV would give you — and
returns a `ToolResult` with `findings`, `warnings`, and `summary`.

## Generated contract, ledger, and conflicts

`--profile contract.json` profiles the input while the normal checks still
run. The JSON contract has one entry per column with a type guess
(`text` / `number` / `date` / `bool`), non-null `count`, `null_rate` (a fraction
of all rows), `distinct_count`, and type-specific detail: `min` / `max` /
`p50` / `p95` for numbers, `freshness.min` / `freshness.max` for dates, and up
to 10 `top_values` for categorical fields. See
[examples/enrichment_contract.json](examples/enrichment_contract.json),
generated from `examples/sample.csv`. Set `options["profile"] = True` in the
library to get the same object at `summary["profile"]` without writing a file.

`--results-log findings.jsonl` appends one JSON line per finding with a UTC
`timestamp`, `severity`, `rule`, `field`, `record_id`, and `message`. The rule
comes from `metadata["rule"]` (or a `check` key) when present, otherwise from
the finding's field. The same behavior is available as `options["results_log"]`.

Rows sharing a record id are compared across `sentiment`, `emotion`, `label`,
`category`, and `source` by default. Differing non-empty values produce one
medium `conflicting_enrichment` finding per id (five examples plus an overflow
note) and `summary["conflict_count"]` with the full count. Override the id
with `--id-column` / `id_column` and the columns with `--conflict-columns` /
`conflict_columns` (a list or comma-separated string). Comparison is
case-insensitive; empty values are ignored.

Engagement rates are in **percentage points**, not fractions: `to_float("12%")`
returns `12`, and a 12-point gap is 12. A trailing percent sign is ignored by
design. With `rate_tolerance > 10`, the tiers are high above
`max(tolerance, 10)` and medium between `min(tolerance, 10)` and that ceiling.

## Limitations, honestly

- Staleness thresholds are yours to set. Seven days is a default, not a
  truth. A monthly pipeline will look "stale" by default; pass
  `--staleness-days 35` and move on.
- The emotion mapping is a fixed table. Your taxonomy may use words it
  doesn't know; unknown labels are reported, not guessed at.
- Benford screening says "investigate," not "guilty." Rounded and
  truncated numbers also trip it. It needs 50+ values and stays quiet
  below that. Up to five eligible columns are tested; the first result stays
  at `summary["benford"]`, with all of them in `summary["benford_all"]`.
  Testing several columns means several comparisons, so a warning is emitted
  and deviations are leads, not verdicts.
- Column matching is heuristic. If your pipeline renames `likes` to
  `engagements_hearted`, tell it with a rename before the scan.

## [0.2.0] - 2026-11-19

### Added

- Generated column contracts via `build_contract(rows)`, `--profile FILE`, and
  `summary["profile"]`; a sample contract lives in `examples/`.
- Append-only check-results ledger via `--results-log FILE` and the
  `results_log` option, with rule names carried in finding metadata.
- Golden-record conflict detection with `id_column` and `conflict_columns`,
  `conflicting_enrichment` findings, and `summary["conflict_count"]`.
- Result-schema contract fields: `result_schema_version`, `skipped`,
  `skip_reason`, `flagged_rows`, `row_errors`, `conflict_count`, and
  `benford_all`. `ToolResult` and `RESULT_SCHEMA_VERSION` are importable from
  the package root.

### Fixed

- Row-level exceptions are counted and warned about (with up to five example
  ids) instead of silently shrinking the audited population.
- Benford screening no longer stops at the first column with 50+ values; it
  tests up to five and warns about multiple comparisons.
- The medium rate-mismatch tier is reachable when `rate_tolerance > 10`.
- `to_float` no longer claims to convert percent signs; rates are percentage
  points.

### Inspiration

- [DataKitchen's 2026 open-source data quality and observability landscape](https://datakitchen.io/blog/the-2026-open-source-data-quality-and-data-observability-landscape)
  — coverage is not capability, and profiling can generate the first draft of
  a test contract.
- [Data quality tooling compared](https://iceberglakehouse.com/posts/data-quality-tooling-compared)
  — a `check_results` table, warn severity, the grain of a check, and pruning
  rules that no longer earn their keep.
- [HN: Get me out of data hell](https://news.ycombinator.com/item?id=42010249)
  — silent failures are the real tax on data pipelines.

## License

MIT. See [LICENSE](LICENSE).
