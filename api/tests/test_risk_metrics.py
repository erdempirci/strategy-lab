"""Risk metrics: hand-checked cases plus a cross-check against quantstats when installed."""

import math

import numpy as np
import pandas as pd
import pytest

from app.services import risk_metrics as rm
from app.services.backtester import run_backtest
from app.services.market_data import demo_ohlcv
from app.strategies.library import ema_crossover

RNG = np.random.default_rng(7)
R = RNG.normal(0.0004, 0.012, 1500)


def test_hand_checked_values():
    r = np.array([0.1, -0.05, 0.02, -0.01])
    assert rm.omega(r) == pytest.approx(0.12 / 0.06)
    down = math.sqrt((0.05 ** 2 + 0.01 ** 2) / 4)
    assert rm.sortino(r, 1) == pytest.approx(r.mean() / down)
    assert rm.cagr(np.array([0.1, 0.1]), 2) == pytest.approx(0.21)
    assert rm.drawdown_series(np.array([0.1, -0.5, 0.2])).min() == pytest.approx(-0.5)


def test_undefined_cases_return_none():
    assert rm.sortino(np.array([0.01, 0.02]), 252) is None
    assert rm.calmar(np.array([0.01, 0.02]), 252) is None
    assert rm.value_at_risk(np.zeros(10)) is None


def test_matches_quantstats():
    qs = pytest.importorskip("quantstats")
    s = pd.Series(R, index=pd.date_range("2020-01-01", periods=len(R), freq="D"))
    p = 365
    assert rm.sortino(R, p) == pytest.approx(qs.stats.sortino(s, periods=p), rel=1e-9)
    assert rm.cagr(R, p) == pytest.approx(qs.stats.cagr(s, periods=p), rel=1e-9)
    assert rm.calmar(R, p) == pytest.approx(qs.stats.calmar(s, periods=p), rel=1e-9)
    assert rm.volatility(R, p) == pytest.approx(qs.stats.volatility(s, periods=p), rel=1e-9)
    assert rm.ulcer_index(R) == pytest.approx(qs.stats.ulcer_index(s), rel=1e-6)
    assert rm.value_at_risk(R) == pytest.approx(qs.stats.value_at_risk(s), rel=1e-9)
    assert rm.expected_shortfall(R) == pytest.approx(qs.stats.conditional_value_at_risk(s), rel=1e-6)
    assert rm.tail_ratio(R) == pytest.approx(qs.stats.tail_ratio(s), rel=1e-9)
    assert rm.omega(R) == pytest.approx(qs.stats.omega(s, periods=1), rel=1e-9)


def test_backtest_reports_risk_block():
    frame = demo_ohlcv(bars=800)
    out = run_backtest(frame, ema_crossover(frame, 12, 26), timeframe="1h")
    m = out["metrics"]
    for k in ("cagr_pct", "sortino_annualized", "calmar", "ulcer_index_pct", "var_95_bar_pct",
              "cvar_95_bar_pct", "tail_ratio", "omega", "volatility_annual_pct"):
        assert k in m
    assert m["cvar_95_bar_pct"] is None or m["cvar_95_bar_pct"] <= m["var_95_bar_pct"]
