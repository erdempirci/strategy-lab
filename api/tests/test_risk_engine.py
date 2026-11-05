import numpy as np
import pandas as pd
import pytest

from app.services.market_data import demo_ohlcv
from app.services.risk_engine import RiskConfig, run_risk_backtest
from app.strategies import STRATEGIES, build_signal


def bars(rows, start="2024-01-01 00:00", freq="1h"):
    """rows: list of (open, high, low, close)."""
    a = np.asarray(rows, float)
    ts = pd.date_range(start, periods=len(a), freq=freq, tz="UTC")
    return pd.DataFrame({"timestamp": ts, "open": a[:, 0], "high": a[:, 1], "low": a[:, 2],
                         "close": a[:, 3], "volume": 1.0})


FLAT = (100, 100.2, 99.8, 100)
PCT2 = RiskConfig(risk_per_trade_pct=0.5, daily_loss_limit_pct=None, max_total_loss_pct=None,
                  stop_type="pct", stop_pct=2.0, take_profit_rr=2.0)


def run(rows, sig, risk=PCT2, **kw):
    return run_risk_backtest(bars(rows), pd.Series(sig, dtype=float), risk,
                             fee_bps=kw.get("fee", 0), slippage_bps=kw.get("slip", 0))


def test_stop_hit_intrabar_loses_exactly_risk():
    # signal 0 -> 1 at bar 1; fill bar 2 open 100; stop 98; qty = 50 / 2 = 25
    res = run([FLAT, FLAT, FLAT, (100, 100.5, 97.5, 98.5), FLAT], [0, 1, 1, 1, 1])
    t = res["trades"][0]
    assert t["exit_reason"] == "stop" and t["exit_price"] == 98 and t["quantity"] == 25
    assert t["pnl"] == pytest.approx(-50) and t["r_multiple"] == pytest.approx(-1)
    assert res["metrics"]["final_equity"] == pytest.approx(9950)


def test_target_at_two_r():
    res = run([FLAT, FLAT, FLAT, (100, 104.5, 99.5, 104)], [0, 1, 1, 1])
    t = res["trades"][0]
    assert t["exit_reason"] == "target" and t["exit_price"] == 104
    assert t["pnl"] == pytest.approx(100) and t["r_multiple"] == pytest.approx(2)


def test_stop_assumed_first_when_both_inside_bar():
    res = run([FLAT, FLAT, FLAT, (100, 105, 97, 101)], [0, 1, 1, 1])
    assert res["trades"][0]["exit_reason"] == "stop"


def test_gap_below_stop_fills_at_open():
    res = run([FLAT, FLAT, FLAT, (95, 96, 94, 95.5)], [0, 1, 1, 1])
    t = res["trades"][0]
    assert t["exit_price"] == 95 and t["pnl"] == pytest.approx(-125)


def test_position_capped_at_one_times_equity():
    tight = RiskConfig(daily_loss_limit_pct=None, max_total_loss_pct=None, stop_type="pct",
                       stop_pct=0.1, take_profit_rr=None)
    res = run([FLAT, FLAT, FLAT, FLAT], [0, 1, 1, 1], risk=tight)
    t = res["trades"][0]
    assert t["quantity"] == pytest.approx(100) and t["position_pct"] == pytest.approx(100)


def test_no_reentry_without_fresh_signal():
    rows = [FLAT, FLAT, FLAT, (100, 100.5, 97.5, 98.5)] + [FLAT] * 4
    res = run(rows, [0, 1, 1, 1, 1, 1, 1, 1])
    assert len(res["trades"]) == 1


def test_signal_exit_at_next_open():
    rows = [FLAT, FLAT, FLAT, (101, 101.5, 100.5, 101), (102, 102.5, 101.5, 102)]
    res = run(rows, [0, 1, 1, 0, 0])
    t = res["trades"][0]
    assert t["exit_reason"] == "signal" and t["exit_price"] == 102


def test_daily_limit_closes_and_blocks_rest_of_day():
    risk = RiskConfig(risk_per_trade_pct=5, daily_loss_limit_pct=3, max_total_loss_pct=None,
                      stop_type="pct", stop_pct=5, take_profit_rr=None)
    # qty capped at 100 units (10000 / 100). Daily floor 9700 -> price 97, above the 95 stop.
    rows = [FLAT, FLAT, FLAT, (100, 100, 96, 96.5)] + [FLAT] * 20 + [FLAT] * 4
    sig = [0, 1, 1, 1, 0, 1, 1, 1] + [1] * 16 + [0, 1, 1, 1]
    res = run(rows, sig, risk=risk)
    first = res["trades"][0]
    assert first["exit_reason"] == "daily_limit" and first["exit_price"] == pytest.approx(97)
    assert res["risk"]["daily_limit_breaches"] == 1
    # The fresh signal at bar 5 (same day) is blocked; the next day's signal trades.
    assert len(res["trades"]) == 2
    assert res["trades"][1]["entry_time"].startswith("2024-01-02")
    assert res["risk"]["worst_day_pct"] == pytest.approx(-3)


def test_max_total_loss_fails_account_and_stops_trading():
    risk = RiskConfig(risk_per_trade_pct=4, daily_loss_limit_pct=None, max_total_loss_pct=10,
                      stop_type="pct", stop_pct=4, take_profit_rr=None)
    loser = [FLAT, (100, 100, 95, 95.5)]          # each trade loses 4% at the 96 stop
    rows, sig = [FLAT], [0]
    for _ in range(5):
        rows += [FLAT] + loser
        sig += [1, 1, 0]
    res = run(rows, sig, risk=risk)
    assert res["risk"]["account_failed"] is True
    assert res["trades"][-1]["exit_reason"] == "max_total_loss"
    assert res["metrics"]["final_equity"] == pytest.approx(9000, abs=0.01)
    assert len(res["trades"]) == 3


@pytest.mark.parametrize("strategy_id", ["ema_crossover", "turtle", "rsi_reversal"])
def test_risk_engine_no_lookahead(strategy_id):
    frame = demo_ohlcv(bars=1500)
    params = STRATEGIES[strategy_id].resolve({}, "1h")
    risk = RiskConfig()
    base = run_risk_backtest(frame, build_signal(strategy_id, frame, params, "1h"), risk)
    tampered = frame.copy()
    tampered.loc[1200:, ["open", "high", "low", "close"]] *= 1.5
    alt = run_risk_backtest(tampered, build_signal(strategy_id, tampered, params, "1h"), risk)
    cutoff = frame["timestamp"].iloc[1199].isoformat()
    closed = lambda r: [t for t in r["trades"] if t["exit_time"] <= cutoff]
    assert closed(base) == closed(alt)
    for t in base["trades"]:
        if t["exit_reason"] == "stop":
            assert t["r_multiple"] == pytest.approx(-1, abs=0.15)  # costs + no gaps in demo data


def test_risk_per_trade_respected_on_real_like_data():
    frame = demo_ohlcv(bars=2000)
    res = run_risk_backtest(frame, build_signal("turtle", frame, {"entry_period": 20,
                                                                  "exit_period": 10}, "1h"),
                            RiskConfig())
    losses = [t["return_pct"] for t in res["trades"] if t["exit_reason"] == "stop"]
    assert losses and min(losses) > -0.6          # ≈ 0.5% risk, small cost/slippage overshoot
    assert res["risk"]["worst_day_pct"] > -3.0001
