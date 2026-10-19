# Contributing

Thanks for considering a contribution. This is a small tool on purpose, and
that's a feature worth protecting.

## Ground rules

- **Keep it stdlib-only at runtime.** No third-party dependencies in
  `src/enrichaudit/`. If a check needs a library the standard library can't
  cover, open an issue before writing the code.
- **One rule, one test.** Every check in `logic.py` earns a test in
  `test_logic.py` with a fixture row that must trigger it, and a clean row
  that must not.
- **No silent behavior changes.** If a finding's severity, wording, or
  trigger conditions change, that goes in the changelog.

## Workflow

1. Open an issue describing the bug or the new rule before starting work on
   anything bigger than a typo fix.
2. Fork, branch, make the change, add tests.
3. Run `python -m pytest` and make sure it's green.
4. Open a pull request that says what changed and why, in plain language.

## Adding a rule

Rules live in `logic.py` as methods on `EnrichAuditLogic`. Each returns
findings through `_finding(severity, field, message, record_id, metadata)`
and appends its rule name to the counts list. Keep messages plain-English and
specific: a finding should tell the reader what is wrong and what numbers
produced the verdict.
