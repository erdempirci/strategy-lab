"""Limit-order entries (e.g. at 50% of the engulfing candle), hand-calculated."""
import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.risk_engine import RiskConfig, run_risk_backtest
from app.strategies.dsl import StrategySpec, describe, stop_levels, strategy_events

client = TestClient(app)
BULL = [{"left": "close", "op": ">", "right": "open"},
        {"left": "close[1]", "op": "<", "right": "open[1]"},
        {"left": "close", "op": ">=", "right": "open[1]"},
        {"left": "open", "op": "<=", "right": "close[1]"}]
BEAR = [{"left": "close", "op": "<", "right": "open"},
        {"left": "close[1]", "op": ">", "right": "open[1]"},
        {"left": "close", "op": "<=", "right": "open[1]"},
        {"left": "open", "op": ">=", "right": "close[1]"}]
LONG = {"name": "Limit", "entry": {"all": BULL}, "stop_at": "low", "take_profit_rr": 1.5,
        "entry_limit": "mid"}
SHORT = {"name": "Limit S", "short_entry": {"all": BEAR}, "short_stop_at": "high",
         "take_profit_rr": 1.5, "short_entry_limit": "mid"}
NO_LIMITS = RiskConfig(daily_loss_limit_pct=None, max_total_loss_pct=None)

FLAT = (101.5, 101.7, 101.3, 101.5)
RED = (101, 101.2, 99.8, 100)
ENG = (99.9, 102.2, 98, 101.5)          # mid = 100.1, low (stop) = 98
GREEN = (99, 100.2, 98.8, 100)
BEARC = (100.1, 102, 97.8, 98.5)        # mid = 99.9, high (stop) = 102


def bars(rows):
    a = np.asarray(rows, float)
    ts = pd.date_range("2024-01-01", periods=len(a), freq="1h", tz="UTC")
    return pd.DataFrame({"timestamp": ts, "open": a[:, 0], "high": a[:, 1], "low": a[:, 2],
                         "close": a[:, 3], "volume": 1.0})


def run(spec_dict, rows):
    spec = StrategySpec.model_validate(spec_dict)
    frame = bars(rows)
    ev = strategy_events(spec, frame)
    return run_risk_backtest(frame, pd.Series(ev["entry"]), NO_LIMITS, fee_bps=0,
                             slippage_bps=0, stop_levels=stop_levels(spec, frame,
                                                                     pd.Series(ev["entry"])),
                             take_profit_rr_override=spec.take_profit_rr, events=ev,
                             limit_levels=ev["limit_levels"],
                             limit_valid_bars=ev["limit_valid_bars"])


def test_long_fills_at_mid_and_target_uses_real_fill():
    out = run(LONG, [FLAT, RED, ENG, (101.6, 101.9, 100.0, 101.0),
                     (101.1, 103.5, 100.9, 103.4), FLAT])
    (t,) = out["trades"]
    assert t["entry_price"] == pytest.approx(100.1)
    assert t["stop_price"] == pytest.approx(98)
    assert t["target_price"] == pytest.approx(100.1 + 1.5 * 2.1)
    assert t["exit_reason"] == "target" and t["exit_price"] == pytest.approx(103.25)
    assert t["r_multiple"] == pytest.approx(1.5, abs=1e-6)
    assert out["risk"]["limit_orders"] == {"placed": 1, "filled": 1, "expired": 0,
                                           "cancelled": 0}


def test_unfilled_limit_expires_after_valid_bars():
    never = (101.0, 101.5, 100.5, 101.0)    # low 100.5 never reaches 100.1
    out = run(LONG, [FLAT, RED, ENG, never, never, never, (101, 101, 99, 100)])
    assert out["trades"] == []
    assert out["risk"]["limit_orders"]["expired"] == 1
    # with a 4-bar window the 4th bar (low 99) fills it
    out = run({**LONG, "limit_valid_bars": 4}, [FLAT, RED, ENG, never, never, never,
                                                (101, 101.2, 99, 100), FLAT])
    assert len(out["trades"]) == 1 and out["trades"][0]["entry_price"] == pytest.approx(100.1)


def test_gap_through_limit_fills_at_open():
    out = run(LONG, [FLAT, RED, ENG, (99.5, 100.5, 99.2, 100), FLAT])
    t = out["trades"][0]
    assert t["entry_price"] == pytest.approx(99.5)
    assert t["target_price"] == pytest.approx(99.5 + 1.5 * 1.5)


def test_stop_on_fill_bar_counts_but_target_does_not():
    out = run(LONG, [FLAT, RED, ENG, (101, 101.2, 97.5, 98.2), FLAT])
    t = out["trades"][0]
    assert t["exit_reason"] == "stop" and t["exit_price"] == pytest.approx(98)
    assert t["r_multiple"] == pytest.approx(-1, abs=1e-6)
    # fill bar reaches the target too: target is NOT taken on that bar (order unknown)
    out = run(LONG, [FLAT, RED, ENG, (101, 104, 100, 101), (101, 101.2, 100.8, 101), FLAT])
    t = out["trades"][0]
    assert t["exit_reason"] == "end_of_data"


def test_short_mirror():
    out = run(SHORT, [FLAT, GREEN, BEARC, (98.6, 100.0, 98.4, 99.0),
                      (99.0, 99.1, 96.5, 96.7), FLAT])
    (t,) = out["trades"]
    assert t["side"] == "short" and t["entry_price"] == pytest.approx(99.9)
    assert t["exit_reason"] == "target" and t["exit_price"] == pytest.approx(99.9 - 3.15)
    assert t["r_multiple"] == pytest.approx(1.5, abs=1e-6)


def test_signal_bar_itself_cannot_fill():
    # the engulfing bar's own low is below its mid; the order only exists from the next bar
    out = run(LONG, [FLAT, RED, ENG, (101.0, 101.5, 100.5, 101.0)])
    assert out["trades"] == []


def test_validation_and_describe():
    with pytest.raises(ValueError):
        StrategySpec.model_validate({**LONG, "entry_limit": "100"})
    with pytest.raises(ValueError):
        StrategySpec.model_validate({**LONG, "short_entry_limit": "mid"})
    rules = describe(StrategySpec.model_validate(LONG))
    assert any("limit emir" in r and "%50" in r for r in rules["risk"])


def test_api_needs_risk_and_runs_with_it():
    body = {"spec": LONG, "bars": 2000}
    r = client.post("/api/custom/backtest", json=body)
    assert r.status_code == 400 and "Limit" in r.json()["detail"]
    risk = {"risk_per_trade_pct": 0.5, "daily_loss_limit_pct": 3, "max_total_loss_pct": 10}
    r = client.post("/api/custom/backtest", json={**body, "risk": risk})
    assert r.status_code == 200, r.text
    lim = r.json()["risk"]["limit_orders"]
    assert lim["placed"] >= lim["filled"] > 0
