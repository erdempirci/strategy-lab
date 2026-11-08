import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.market_data import demo_ohlcv
from app.services.risk_engine import RiskConfig, run_risk_backtest
from app.strategies.dsl import StrategySpec, strategy_events

client = TestClient(app)
BULL = [{"left": "close", "op": ">", "right": "open"},
        {"left": "close[1]", "op": "<", "right": "open[1]"},
        {"left": "close", "op": ">=", "right": "open[1]"},
        {"left": "open", "op": "<=", "right": "close[1]"}]
# Exactly the user's case: long engulfing, stop at the wick, 1:1.5, NO exit rule.
USER = {"name": "Yutan Mum Stratejisi", "entry": {"all": BULL}, "stop_at": "low",
        "take_profit_rr": 1.5}
NO_LIMITS = RiskConfig(daily_loss_limit_pct=None, max_total_loss_pct=None)


def bars(rows):
    a = np.asarray(rows, float)
    ts = pd.date_range("2024-01-01", periods=len(a), freq="1h", tz="UTC")
    return pd.DataFrame({"timestamp": ts, "open": a[:, 0], "high": a[:, 1], "low": a[:, 2],
                         "close": a[:, 3], "volume": 1.0})


RED = (101, 101.2, 99.8, 100)
ENG = (99.9, 102.2, 98, 101.5)
FLAT = (101.5, 101.7, 101.3, 101.5)


def test_reenters_on_every_new_pattern_after_stop_or_target():
    # engulf -> stopped out -> later another engulf -> target
    rows = [FLAT, RED, ENG, (101.6, 101.8, 97.5, 98.0),          # stopped at 98
            FLAT, RED, ENG, (101.6, 107.5, 101.4, 107.2), FLAT]  # target 107.0
    frame = bars(rows)
    spec = StrategySpec.model_validate(USER)
    ev = strategy_events(spec, frame)
    assert list(np.flatnonzero(ev["entry"])) == [2, 6]
    from app.strategies.dsl import stop_levels
    res = run_risk_backtest(frame, pd.Series(np.zeros(len(frame))), NO_LIMITS, fee_bps=0,
                            slippage_bps=0, stop_levels=stop_levels(spec, frame),
                            take_profit_rr_override=1.5, events=ev)
    assert [t["exit_reason"] for t in res["trades"]] == ["stop", "target"]


def test_rising_edge_no_reentry_while_setup_stays_true():
    spec = StrategySpec.model_validate({"indicators": [{"id": "e", "type": "ema", "period": 3}],
                                        "entry": {"all": [{"left": "close", "op": ">", "right": "e"}]},
                                        "stop_loss_pct": 1})
    closes = [100, 100, 100, 101, 102, 103, 104, 105, 106]
    frame = bars([(c, c * 1.001, c * 0.999, c) for c in closes])
    ev = strategy_events(spec, frame)
    assert int((ev["entry"] != 0).sum()) == 1


def test_user_case_has_many_trades_on_long_history():
    frame = demo_ohlcv("US100", "1h", 11_000)
    spec = StrategySpec.model_validate(USER)
    n_patterns = int((strategy_events(spec, frame)["entry"] == 1).sum())
    body = client.post("/api/custom/backtest", json={
        "spec": USER, "bars": 11_000, "symbol": "US100", "fee_bps": 0.5, "slippage_bps": 0.5,
        "risk": {"risk_per_trade_pct": 0.5, "daily_loss_limit_pct": None,
                 "max_total_loss_pct": None}}).json()
    trades = body["metrics"]["trades"]
    assert n_patterns > 300
    # Every pattern while flat becomes a trade; overlapping ones are skipped.
    assert trades > n_patterns * 0.4, (trades, n_patterns)
    assert {t["exit_reason"] for t in body["trades"]} <= {"stop", "target", "end_of_data"}


def test_time_exit_and_rule_exit_with_events():
    spec = StrategySpec.model_validate({**USER, "take_profit_rr": None, "max_bars_in_trade": 2})
    rows = [FLAT, RED, ENG, (101.6, 101.9, 101.2, 101.7), (101.7, 101.9, 101.3, 101.6),
            (101.6, 101.8, 101.4, 101.5), FLAT]
    frame = bars(rows)
    from app.strategies.dsl import stop_levels
    res = run_risk_backtest(frame, pd.Series(np.zeros(len(frame))), NO_LIMITS, fee_bps=0,
                            slippage_bps=0, stop_levels=stop_levels(spec, frame), events=strategy_events(spec, frame))
    t = res["trades"][0]
    assert t["exit_reason"] == "time" and t["bars_held"] == 2
    assert res["risk"]["exit_reasons"]["time"] == 1


@pytest.mark.parametrize("cut", [1200, 1700])
def test_events_no_lookahead(cut):
    frame = demo_ohlcv(bars=2000)
    spec = StrategySpec.model_validate(USER)
    a = strategy_events(spec, frame)["entry"]
    t = frame.copy()
    t.loc[cut + 1:, ["open", "high", "low", "close"]] *= 1.4
    b = strategy_events(spec, t)["entry"]
    np.testing.assert_array_equal(a[:cut + 1], b[:cut + 1])
