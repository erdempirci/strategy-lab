import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from app.services.market_data import demo_ohlcv
from app.services.risk_engine import RiskConfig, run_risk_backtest
from app.strategies.dsl import StrategySpec, _Context, custom_signal, describe, stop_levels

client = TestClient(app)

# The user's strategy: H1 bullish engulfing that left a lower wick; enter next bar,
# stop at the engulfing candle's low (wick), target 1:1.5.
WICK_ENGULF = {
    "name": "Fitilli yutan boğa",
    "entry": {"all": [
        {"left": "close", "op": ">", "right": "open"},
        {"left": "close[1]", "op": "<", "right": "open[1]"},
        {"left": "close", "op": ">=", "right": "open[1]"},
        {"left": "open", "op": "<=", "right": "close[1]"},
        {"left": "lower_wick", "op": ">", "right": "body", "right_mult": 0.3},
    ]},
    "stop_at": "low",
    "take_profit_rr": 1.5,
}


def bars(rows):
    a = np.asarray(rows, float)
    ts = pd.date_range("2024-01-01", periods=len(a), freq="1h", tz="UTC")
    return pd.DataFrame({"timestamp": ts, "open": a[:, 0], "high": a[:, 1], "low": a[:, 2],
                         "close": a[:, 3], "volume": 1.0})


FLAT = (100, 100.5, 99.5, 100)
RED = (101, 101.2, 99.8, 100)           # bearish candle
ENGULF = (99.9, 102.2, 98, 101.5)       # opens below prior close, closes above prior open, wick to 98


def test_candle_anatomy_and_right_mult():
    frame = bars([ENGULF])
    ctx = _Context(StrategySpec.model_validate({**WICK_ENGULF}), frame)
    assert ctx.operand("body").iloc[0] == pytest.approx(1.6)
    assert ctx.operand("lower_wick").iloc[0] == pytest.approx(1.9)
    assert ctx.operand("upper_wick").iloc[0] == pytest.approx(0.7)
    assert ctx.operand("range").iloc[0] == pytest.approx(4.2)
    text = describe(StrategySpec.model_validate(WICK_ENGULF))
    assert any("alt fitil büyüktür gövde × 0.3" in line for line in text["entry"])
    assert any("düşük" in line for line in text["risk"]) and any("1:1.5" in r for r in text["risk"])


def test_risk_engine_stop_at_wick_and_rr_target():
    # bar 2 engulfing (signal) -> fill bar 3 open 101.6; stop 98 (wick); risk 3.6; target 107.0
    rows = [FLAT, RED, ENGULF, (101.6, 103, 101, 102.5), (102.5, 107.5, 102, 107)]
    frame = bars(rows)
    spec = StrategySpec.model_validate(WICK_ENGULF)
    sig = custom_signal(spec, frame, apply_stops=False)
    res = run_risk_backtest(frame, sig, RiskConfig(daily_loss_limit_pct=None, max_total_loss_pct=None),
                            fee_bps=0, slippage_bps=0, stop_levels=stop_levels(spec, frame),
                            take_profit_rr_override=1.5)
    t = res["trades"][0]
    assert t["entry_price"] == pytest.approx(101.6)
    assert t["stop_price"] == pytest.approx(98.0)
    assert t["target_price"] == pytest.approx(101.6 + 1.5 * 3.6)
    assert t["exit_reason"] == "target" and t["r_multiple"] == pytest.approx(1.5)
    # 0.5% risk of 10,000 = 50 at a 3.6 stop distance -> 13.89 units
    assert t["quantity"] == pytest.approx(50 / 3.6, rel=1e-6)


def test_risk_engine_stop_at_wick_is_hit_intrabar():
    rows = [FLAT, RED, ENGULF, (101.6, 102, 97.5, 98.5), FLAT]
    frame = bars(rows)
    spec = StrategySpec.model_validate(WICK_ENGULF)
    res = run_risk_backtest(frame, custom_signal(spec, frame, apply_stops=False),
                            RiskConfig(daily_loss_limit_pct=None, max_total_loss_pct=None),
                            fee_bps=0, slippage_bps=0, stop_levels=stop_levels(spec, frame),
                            take_profit_rr_override=1.5)
    t = res["trades"][0]
    assert t["exit_reason"] == "stop" and t["exit_price"] == pytest.approx(98.0)
    assert t["r_multiple"] == pytest.approx(-1)


def test_simple_engine_close_based_stop_and_target():
    rows = [FLAT, RED, ENGULF, (101.6, 103, 101, 102.5), (102.5, 107.5, 102, 107.2), FLAT]
    sig = custom_signal(StrategySpec.model_validate(WICK_ENGULF), bars(rows))
    assert sig[2] == 1 and sig[3] == 1
    assert sig[4] == 0                       # close 107.2 >= target 107.0


def test_structural_stop_no_lookahead():
    frame = demo_ohlcv(bars=2000)
    spec = StrategySpec.model_validate({**WICK_ENGULF, "entry": {"all": WICK_ENGULF["entry"]["all"][:4]}})
    a = custom_signal(spec, frame)
    t = frame.copy()
    t.loc[1501:, ["open", "high", "low", "close"]] *= 0.8
    pd.testing.assert_series_equal(a.iloc[:1501], custom_signal(spec, t).iloc[:1501])


@pytest.mark.parametrize("bad", [
    {**WICK_ENGULF, "stop_at": "30"},
    {**WICK_ENGULF, "stop_at": "nope"},
    {**WICK_ENGULF, "stop_loss_pct": 1},
    {**WICK_ENGULF, "take_profit_pct": 2},
    {"entry": WICK_ENGULF["entry"], "take_profit_rr": 2, "max_bars_in_trade": 5},
])
def test_invalid_structural_specs(bad):
    with pytest.raises(ValidationError):
        StrategySpec.model_validate(bad)


def test_api_custom_backtest_with_risk_uses_wick_stop():
    spec = {**WICK_ENGULF, "entry": {"all": WICK_ENGULF["entry"]["all"][:4]}}
    body = client.post("/api/custom/backtest", json={
        "spec": spec, "bars": 3000, "risk": {"risk_per_trade_pct": 0.5}}).json()
    assert body["trades"], body
    for t in body["trades"]:
        if t["exit_reason"] == "target":
            risk = t["entry_price"] - t["stop_price"]
            assert t["target_price"] == pytest.approx(t["entry_price"] + 1.5 * risk, rel=1e-6)
