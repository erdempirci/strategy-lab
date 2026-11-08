import numpy as np
import pandas as pd
import pytest

from app.services.backtester import run_backtest
from app.services.risk_engine import RiskConfig, run_risk_backtest


def bars(rows, start="2024-01-01 00:00"):
    a = np.asarray(rows, float)
    if a.ndim == 1:
        a = np.column_stack([a, a, a, a])
    ts = pd.date_range(start, periods=len(a), freq="1h", tz="UTC")
    return pd.DataFrame({"timestamp": ts, "open": a[:, 0], "high": a[:, 1], "low": a[:, 2],
                         "close": a[:, 3], "volume": 1.0})


FLAT = (100, 100.2, 99.8, 100)
NO_LIMITS = {"daily_loss_limit_pct": None, "max_total_loss_pct": None}


# ------------------------------------------------------------------ simple engine
def test_simple_short_profit_hand_calculated():
    # short signal at bar 1 -> sell at bar 2 open 100; cover at bar 4 open 90
    frame = bars([100, 100, 100, 95, 90, 90])
    res = run_backtest(frame, pd.Series([0, -1, -1, 0, 0, 0], dtype=float),
                       initial_capital=1000, fee_bps=0, slippage_bps=0)
    t = res["trades"][0]
    assert t["side"] == "short" and t["quantity"] == pytest.approx(10)
    assert t["pnl"] == pytest.approx(100) and res["metrics"]["final_equity"] == pytest.approx(1100)


def test_simple_short_equity_marked_to_market():
    frame = bars([100, 100, 100, 110, 110])
    res = run_backtest(frame, pd.Series([0, -1, -1, -1, -1], dtype=float),
                       initial_capital=1000, fee_bps=0, slippage_bps=0)
    assert res["metrics"]["final_equity"] == pytest.approx(900)   # 10 units, +10 against us


def test_simple_flip_long_to_short_same_open():
    frame = bars([100, 100, 100, 110, 110, 100, 100])
    sig = pd.Series([0, 1, 1, -1, -1, 0, 0], dtype=float)
    res = run_backtest(frame, sig, initial_capital=1000, fee_bps=0, slippage_bps=0)
    long_, short = res["trades"]
    assert long_["side"] == "long" and long_["exit_time"] == short["entry_time"]
    assert long_["pnl"] == pytest.approx(100)              # 100 -> 110 on 1000
    assert short["pnl"] == pytest.approx(100)              # 1100 short at 110 -> 100
    assert res["metrics"]["final_equity"] == pytest.approx(1200)


def test_simple_short_costs_hand_calculated():
    frame = bars([100, 100, 100, 95, 90, 90])
    res = run_backtest(frame, pd.Series([0, -1, -1, 0, 0, 0], dtype=float),
                       initial_capital=1000, fee_bps=10, slippage_bps=20)
    sell, buy, fee = 100 * 0.998, 90 * 1.002, 0.001
    q = 1000 / (sell * (1 + fee))
    cash = 1000 + q * sell - q * sell * fee - q * buy - q * buy * fee
    assert res["metrics"]["final_equity"] == pytest.approx(cash, abs=0.01)


# -------------------------------------------------------------------- risk engine
PCT2 = RiskConfig(stop_type="pct", stop_pct=2, take_profit_rr=2, **NO_LIMITS)


def risk_run(rows, sig, risk=PCT2):
    return run_risk_backtest(bars(rows), pd.Series(sig, dtype=float), risk,
                             fee_bps=0, slippage_bps=0)


def test_short_stop_above_entry_loses_exactly_1r():
    # sell at 100, stop 102, 0.5% of 10k = 50 -> 25 units
    res = risk_run([FLAT, FLAT, FLAT, (100, 102.5, 99.5, 101.5), FLAT], [0, -1, -1, -1, -1])
    t = res["trades"][0]
    assert t["side"] == "short" and t["stop_price"] == 102 and t["quantity"] == 25
    assert t["exit_reason"] == "stop" and t["pnl"] == pytest.approx(-50)
    assert t["r_multiple"] == pytest.approx(-1)


def test_short_target_two_r_below():
    res = risk_run([FLAT, FLAT, FLAT, (100, 100.5, 95.5, 96)], [0, -1, -1, -1])
    t = res["trades"][0]
    assert t["exit_reason"] == "target" and t["exit_price"] == 96
    assert t["pnl"] == pytest.approx(100) and t["r_multiple"] == pytest.approx(2)


def test_short_gap_above_stop_fills_at_open():
    res = risk_run([FLAT, FLAT, FLAT, (105, 106, 104, 105)], [0, -1, -1, -1])
    assert res["trades"][0]["exit_price"] == 105 and res["trades"][0]["pnl"] == pytest.approx(-125)


def test_short_stop_first_when_both_inside_bar():
    res = risk_run([FLAT, FLAT, FLAT, (100, 103, 95, 99)], [0, -1, -1, -1])
    assert res["trades"][0]["exit_reason"] == "stop"


def test_short_structural_stop_at_high():
    rows = [FLAT, FLAT, (100, 101.5, 99.5, 100), (100, 100.5, 96, 97)]
    levels = np.array([np.nan, np.nan, 101.5, np.nan])        # signal bar's high = wick
    res = run_risk_backtest(bars(rows), pd.Series([0, 0, -1, -1], dtype=float),
                            RiskConfig(**NO_LIMITS), fee_bps=0, slippage_bps=0,
                            stop_levels=levels, take_profit_rr_override=1.5)
    t = res["trades"][0]
    assert t["stop_price"] == 101.5 and t["target_price"] == pytest.approx(100 - 1.5 * 1.5)
    assert t["exit_reason"] == "target"


def test_short_daily_limit_triggers_on_rally():
    risk = RiskConfig(risk_per_trade_pct=5, daily_loss_limit_pct=3, max_total_loss_pct=None,
                      stop_type="pct", stop_pct=5, take_profit_rr=None)
    # 100 units short at 100 (1x cap); floor 9700 -> buy back at 103 before the 105 stop
    res = risk_run([FLAT, FLAT, FLAT, (100, 104, 99.5, 103.5), FLAT], [0, -1, -1, -1, -1], risk)
    t = res["trades"][0]
    assert t["exit_reason"] == "daily_limit" and t["exit_price"] == pytest.approx(103)
    assert res["risk"]["worst_day_pct"] == pytest.approx(-3)


def test_flip_long_to_short_in_risk_engine():
    rows = [FLAT, FLAT, FLAT, FLAT, FLAT, FLAT]
    res = risk_run(rows, [0, 1, 1, -1, -1, -1])
    sides = [t["side"] for t in res["trades"]]
    assert sides == ["long", "short"]
    assert res["trades"][0]["exit_reason"] == "signal"
    assert res["trades"][0]["exit_time"] == res["trades"][1]["entry_time"]


# --------------------------------------------------------- DSL: two-sided engulfing
from app.strategies.dsl import StrategySpec, custom_signal, describe, stop_levels

BULL = [{"left": "close", "op": ">", "right": "open"},
        {"left": "close[1]", "op": "<", "right": "open[1]"},
        {"left": "close", "op": ">=", "right": "open[1]"},
        {"left": "open", "op": "<=", "right": "close[1]"}]
BEAR = [{"left": "close", "op": "<", "right": "open"},
        {"left": "close[1]", "op": ">", "right": "open[1]"},
        {"left": "close", "op": "<=", "right": "open[1]"},
        {"left": "open", "op": ">=", "right": "close[1]"}]
BOTH = {"name": "Yutan mum (iki yön)", "entry": {"all": BULL}, "short_entry": {"all": BEAR},
        "stop_at": "low", "short_stop_at": "high", "take_profit_rr": 1.5}

UP = (100, 100.6, 99.9, 100.5)                 # green
BEAR_ENG = (100.6, 101.8, 99.6, 99.8)          # red, engulfs UP; upper wick to 101.8


def test_bearish_engulfing_opens_short_with_wick_stop():
    rows = [FLAT, UP, BEAR_ENG, (99.8, 100.2, 96.5, 97.0), FLAT]
    frame = bars(rows)
    spec = StrategySpec.model_validate(BOTH)
    sig = custom_signal(spec, frame, apply_stops=False)
    assert sig[2] == -1
    res = run_risk_backtest(frame, sig, RiskConfig(**NO_LIMITS), fee_bps=0, slippage_bps=0,
                            stop_levels=stop_levels(spec, frame, sig), take_profit_rr_override=1.5)
    t = res["trades"][0]
    assert t["side"] == "short" and t["entry_price"] == pytest.approx(99.8)
    assert t["stop_price"] == pytest.approx(101.8)
    assert t["target_price"] == pytest.approx(99.8 - 1.5 * 2.0)
    assert t["exit_reason"] == "target" and t["r_multiple"] == pytest.approx(1.5)


def test_opposite_engulfing_flips_position():
    RED = (101, 101.2, 99.8, 100)
    BULL_ENG = (99.9, 102.2, 98, 101.5)
    rows = [FLAT, RED, BULL_ENG, (101.5, 101.6, 101.2, 101.4), UP, BEAR_ENG, FLAT, FLAT]
    frame = bars(rows)
    frame.loc[3, ["open", "high", "low", "close"]] = [101.5, 101.6, 101.0, 100.4]   # red bar
    frame.loc[4, ["open", "high", "low", "close"]] = [100.3, 101.0, 100.2, 100.9]   # green
    frame.loc[5, ["open", "high", "low", "close"]] = [101.0, 101.8, 99.6, 100.1]    # bear engulf
    sig = custom_signal(StrategySpec.model_validate({**BOTH, "take_profit_rr": None,
                                                     "max_bars_in_trade": 50}), frame,
                        apply_stops=False)
    assert sig[2] == 1 and sig[5] == -1


def test_describe_lists_short_rules():
    text = describe(StrategySpec.model_validate(BOTH))
    assert text["short_entry"] and any("Short stop" in r for r in text["risk"])


@pytest.mark.parametrize("bad", [
    {**BOTH, "short_stop_at": None},                    # two sides need both stops
    {"short_entry": {"all": BEAR}, "stop_at": "high"},  # stop_at is for longs
    {"entry": {"all": BULL}, "short_stop_at": "high", "stop_at": "low"},  # no short entry
    {"short_entry": {"all": BEAR}},                     # no short exit
])
def test_invalid_short_specs(bad):
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        StrategySpec.model_validate(bad)


def test_short_only_spec_and_no_lookahead():
    from app.services.market_data import demo_ohlcv
    spec = StrategySpec.model_validate({"short_entry": {"all": BEAR}, "short_stop_at": "high",
                                        "take_profit_rr": 2})
    frame = demo_ohlcv(bars=2000)
    a = custom_signal(spec, frame)
    assert set(a.dropna().unique()) <= {0.0, -1.0} and (a == -1).any()
    t = frame.copy()
    t.loc[1501:, ["open", "high", "low", "close"]] *= 1.3
    pd.testing.assert_series_equal(a.iloc[:1501], custom_signal(spec, t).iloc[:1501])


def test_api_two_sided_custom_backtest():
    from fastapi.testclient import TestClient

    from app.main import app
    body = TestClient(app).post("/api/custom/backtest", json={
        "spec": BOTH, "bars": 3000, "risk": {"risk_per_trade_pct": 0.5}}).json()
    sides = {t["side"] for t in body["trades"]}
    assert sides == {"long", "short"}
    for t in body["trades"]:
        if t["exit_reason"] == "stop" and t["side"] == "short":
            assert t["stop_price"] > t["entry_price"]
