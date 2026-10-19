"""Statistical primitives EnrichAudit needs, stdlib-only and deterministic.

Each helper documents the minimum sample size it requires — the rules respect
those gates so small uploads don't produce noise findings.
"""

from __future__ import annotations

import math

# ---------------------------------------------------------------------------
# Robust descriptive statistics
# ---------------------------------------------------------------------------


def median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def mad(values: list[float]) -> float | None:
    """Median absolute deviation — robust spread estimator."""
    med = median(values)
    if med is None:
        return None
    return median([abs(v - med) for v in values])


def modified_zscores(values: list[float]) -> list[float]:
    """MAD-based modified z-scores (Iglewicz & Hoaglin).

    Robust to the very outliers it detects, unlike mean-based z-scores.
    When MAD is 0 (majority of points identical), falls back to the mean
    absolute deviation so a lone extreme still scores; all-identical input
    yields 0.0s (no spread to measure against).
    """
    med = median(values)
    if med is None:
        return [0.0 for _ in values]
    spread = mad(values)
    if not spread:
        spread = sum(abs(v - med) for v in values) / len(values)
    if not spread:
        return [0.0 for _ in values]
    return [0.6745 * (v - med) / spread for v in values]


# ---------------------------------------------------------------------------
# Chi-square machinery (needed for Benford screening)
# ---------------------------------------------------------------------------


def _gamma_p(a: float, x: float) -> float:
    """Regularized lower incomplete gamma P(a, x) (Numerical Recipes gser)."""
    if x <= 0:
        return 0.0
    gln = math.lgamma(a)
    ap = a
    term = 1.0 / a
    total = term
    for _ in range(1, 500):
        ap += 1
        term *= x / ap
        total += term
        if abs(term) < abs(total) * 1e-12:
            break
    return total * math.exp(-x + a * math.log(x) - gln)


def _gamma_q(a: float, x: float) -> float:
    """Regularized upper incomplete gamma Q(a, x) via continued fraction (gcf)."""
    if x <= 0:
        return 1.0
    gln = math.lgamma(a)
    b = x + 1.0 - a
    c, d = 1e30, 1.0 / b
    h = d
    for i in range(1, 500):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        d = 1e-30 if abs(d) < 1e-30 else d
        c = b + an / c
        c = 1e-30 if abs(c) < 1e-30 else c
        d = 1.0 / d
        delta = c * d
        h *= delta
        if abs(delta - 1.0) < 1e-12:
            break
    return math.exp(-x + a * math.log(x) - gln) * h


def chi_square_sf(statistic: float, dof: int) -> float:
    """Survival function (p-value) of the chi-square distribution."""
    if statistic <= 0 or dof <= 0:
        return 1.0
    if statistic < dof + 1:
        return 1.0 - _gamma_p(dof / 2.0, statistic / 2.0)
    return _gamma_q(dof / 2.0, statistic / 2.0)


def chi_square_gof(observed: list[float], expected: list[float]) -> tuple[float, int]:
    """Goodness-of-fit statistic and degrees of freedom."""
    stat = sum((o - e) ** 2 / e for o, e in zip(observed, expected) if e > 0)
    return stat, max(len(observed) - 1, 1)


# ---------------------------------------------------------------------------
# Benford's law (fabricated-number screening)
# ---------------------------------------------------------------------------

BENFORD_EXPECTED = [math.log10(1 + 1 / d) for d in range(1, 10)]


def first_digit(value: float) -> int | None:
    v = abs(value)
    if v <= 0 or not math.isfinite(v):
        return None
    while v >= 10:
        v /= 10
    while v < 1:
        v *= 10
    return int(v)


def benford_chi_square(values: list[float]) -> tuple[float, int, int] | None:
    """Chi-square of first-digit distribution vs Benford (needs n >= 50)."""
    digits = [d for v in values if (d := first_digit(v)) is not None]
    if len(digits) < 50:
        return None
    observed = [sum(1 for d in digits if d == k) for k in range(1, 10)]
    expected = [BENFORD_EXPECTED[k] * len(digits) for k in range(9)]
    stat, dof = chi_square_gof(observed, expected)
    return stat, dof, len(digits)
