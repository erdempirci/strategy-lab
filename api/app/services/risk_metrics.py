"""Return-distribution risk metrics.

Formulas follow ranaroussi/quantstats (Apache-2.0, `quantstats/stats.py`) so results can be
checked against that library: tests/test_risk_metrics.py compares every value when
quantstats is installed. Implemented here directly (numpy + statistics only) to keep the
API dependency-free and fast on Vercel.

All inputs are per-bar simple returns; `periods` is the number of bars per year
(`app.services.timeframes.periods_per_year`).
"""

from __future__ import annotations

import math
from statistics import NormalDist

import numpy as np

_N = NormalDist()


def _clean(r) -> np.ndarray:
    a = np.asarray(r, dtype=float)
    return a[np.isfinite(a)]


def _fin(x: float | None, digits: int = 4) -> float | None:
    return round(float(x), digits) if x is not None and math.isfinite(x) else None


def sortino(r, periods: float) -> float | None:
    """Mean ÷ downside deviation (zero target, all observations in the denominator), annualized."""
    r = _clean(r)
    if len(r) < 2:
        return None
    downside = math.sqrt(float((r[r < 0] ** 2).sum()) / len(r))
    return float(r.mean()) / downside * math.sqrt(periods) if downside > 0 else None


def volatility(r, periods: float) -> float | None:
    r = _clean(r)
    return float(r.std(ddof=1)) * math.sqrt(periods) if len(r) > 1 else None


def cagr(r, periods: float) -> float | None:
    """Compound annual growth, years = number of bars ÷ bars per year."""
    r = _clean(r)
    if not len(r):
        return None
    wealth = float(np.prod(1 + r))
    years = len(r) / periods
    if wealth <= 0 or years <= 0:
        return None
    try:  # a few bars of data annualize to astronomically large numbers
        return wealth ** (1 / years) - 1
    except OverflowError:
        return None


def drawdown_series(r) -> np.ndarray:
    prices = np.cumprod(1 + _clean(r))
    return prices / np.maximum.accumulate(prices) - 1


def calmar(r, periods: float) -> float | None:
    g, dd = cagr(r, periods), drawdown_series(r)
    mdd = float(dd.min()) if len(dd) else 0.0
    return g / abs(mdd) if g is not None and mdd < 0 else None


def ulcer_index(r) -> float | None:
    """Root-mean-square drawdown (depth and duration of pain), as a fraction."""
    dd = drawdown_series(r)
    return math.sqrt(float((dd ** 2).sum()) / (len(dd) - 1)) if len(dd) > 1 else None


def value_at_risk(r, confidence: float = 0.95) -> float | None:
    """Parametric (normal) one-bar VaR; negative number = loss."""
    r = _clean(r)
    if len(r) < 2 or r.std(ddof=1) == 0:
        return None
    return NormalDist(float(r.mean()), float(r.std(ddof=1))).inv_cdf(1 - confidence)


def expected_shortfall(r, confidence: float = 0.95) -> float | None:
    """Parametric (normal) one-bar CVaR / expected shortfall; mean loss beyond VaR."""
    r = _clean(r)
    if len(r) < 2 or r.std(ddof=1) == 0:
        return None
    alpha = 1 - confidence
    z = _N.inv_cdf(alpha)
    return float(r.mean()) - float(r.std(ddof=1)) * _N.pdf(z) / alpha


def tail_ratio(r, cutoff: float = 0.95) -> float | None:
    """|95th percentile| ÷ |5th percentile|: >1 means right tail (gains) is fatter than left."""
    r = _clean(r)
    if len(r) < 2:
        return None
    lo = float(np.quantile(r, 1 - cutoff))
    return abs(float(np.quantile(r, cutoff)) / lo) if lo != 0 else None


def omega(r) -> float | None:
    """Σ gains ÷ Σ losses above/below a zero threshold."""
    r = _clean(r)
    down = -float(r[r < 0].sum())
    return float(r[r > 0].sum()) / down if down > 0 else None


def risk_metrics(bar_returns, periods: float) -> dict:
    """The block merged into every backtest's `metrics`. Percentages are in percent."""
    r = _clean(bar_returns)
    def pct(x: float | None) -> float | None:
        return None if x is None else x * 100

    return {
        "cagr_pct": _fin(pct(cagr(r, periods))),
        "volatility_annual_pct": _fin(pct(volatility(r, periods))),
        "sortino_annualized": _fin(sortino(r, periods)),
        "calmar": _fin(calmar(r, periods)),
        "ulcer_index_pct": _fin(pct(ulcer_index(r))),
        "var_95_bar_pct": _fin(pct(value_at_risk(r))),
        "cvar_95_bar_pct": _fin(pct(expected_shortfall(r))),
        "tail_ratio": _fin(tail_ratio(r)),
        "omega": _fin(omega(r)),
    }
