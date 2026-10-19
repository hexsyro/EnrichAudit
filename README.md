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
enrichaudit 0.1.0 — 7 finding(s), 10 row(s) checked

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
| `--config FILE` | JSON file with any of the same keys (`staleness_days`, `rate_tolerance`, `check_sentiment_emotion`, `as_of`); explicit flags win |
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
  "version": "0.1.0",
  "status": "success",
  "findings": [
    {
      "severity": "high",
      "field": "engagement_rate",
      "message": "Engagement rate 306.00% is above 100%, which is impossible.",
      "record_id": "5",
      "metadata": {"given_rate": 306.0}
    }
  ],
  "warnings": [],
  "summary": {"checked": 10, "flagged": 5, "rule_counts": {"impossible_rate": 1}}
}
```

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

## Limitations, honestly

- Staleness thresholds are yours to set. Seven days is a default, not a
  truth. A monthly pipeline will look "stale" by default; pass
  `--staleness-days 35` and move on.
- The emotion mapping is a fixed table. Your taxonomy may use words it
  doesn't know; unknown labels are reported, not guessed at.
- Benford screening says "investigate," not "guilty." Rounded and
  truncated numbers also trip it. It needs 50+ values and stays quiet
  below that.
- Column matching is heuristic. If your pipeline renames `likes` to
  `engagements_hearted`, tell it with a rename before the scan.

## License

MIT. See [LICENSE](LICENSE).
