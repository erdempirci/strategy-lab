import math

import numpy as np
import pandas as pd
import pytest

from app.services.backtester import ema_crossover_signal, run_backtest, run_ema_backtest
from app.services.market_data import demo_ohlcv


def make_frame(opens, closes=None):
    opens = np.asarray(opens, dtype=float)
    closes = opens if closes is None else np.asarray(closes, dtype=float)
    ts = pd.date_range("2024-01-01", periods=len(opens), freq="1h", tz="UTC")
    return pd.DataFrame({
        "timestamp": ts, "open": opens, "high": np.maximum(opens, closes) * 1.001,
        "low": np.minimum(opens, closes) * 0.999, "close": closes, "volume": 1.0,
    })


def test_demo_data_is_deterministic_except_timestamp():
    a = demo_ohlcv("BTC/USDT", "1h", 120)
    b = demo_ohlcv("BTC/USDT", "1h", 120)
    pd.testing.assert_series_equal(a["close"], b["close"])


def test_demo_timestamps_follow_timeframe():
    frame = demo_ohlcv("BTC/USDT", "4h", 10)
    assert (frame["timestamp"].diff().dropna() == pd.Timedelta(hours=4)).all()


def test_single_trade_no_costs_hand_calculated():
    # Signal long at close of bar 1 -> buy at open of bar 2 (100).
    # Signal flat at close of bar 3 -> sell at open of bar 4 (110).
    frame = make_frame([90, 95, 100, 105, 110, 108])
    signal = pd.Series([0, 1, 1, 0, 0, 0], dtype=float)
    res = run_backtest(frame, signal, initial_capital=1000, fee_bps=0, slippage_bps=0)
    assert len(res["trades"]) == 1
    trade = res["trades"][0]
    assert trade["entry_price"] == 100 and trade["exit_price"] == 110
    assert trade["pnl"] == pytest.approx(100.0)
    assert res["metrics"]["final_equity"] == pytest.approx(1100.0)
    assert res["metrics"]["win_rate_pct"] == 100.0


def test_fees_and_slippage_hand_calculated():
    frame = make_frame([90, 95, 100, 105, 110, 108])
    signal = pd.Series([0, 1, 1, 0, 0, 0], dtype=float)
    res = run_backtest(frame, signal, initial_capital=1000, fee_bps=10, slippage_bps=20)
    buy, sell, fee = 100 * 1.002, 110 * 0.998, 0.001
    qty = 1000 / (buy * (1 + fee))
    expected_cash = qty * sell * (1 - fee)
    assert res["metrics"]["final_equity"] == pytest.approx(expected_cash, abs=0.01)
    assert res["trades"][0]["pnl"] == pytest.approx(expected_cash - 1000, abs=1e-3)


def test_win_rate_and_profit_factor_are_per_trade():
    # Trade 1: 100 -> 110 (+10%). Trade 2: 100 -> 95 (-5%, on 1100 capital = -55).
    frame = make_frame([100, 100, 110, 110, 100, 100, 95, 95])
    signal = pd.Series([1, 0, 0, 1, 1, 0, 0, 0], dtype=float)
    res = run_backtest(frame, signal, initial_capital=1000, fee_bps=0, slippage_bps=0)
    m = res["metrics"]
    assert m["trades"] == 2
    assert m["win_rate_pct"] == 50.0
    assert m["profit_factor"] == pytest.approx(100 / 55, rel=1e-4)
    assert m["max_consecutive_losses"] == 1
    assert m["final_equity"] == pytest.approx(1045.0)


def test_open_position_is_force_closed_at_last_close():
    frame = make_frame([100, 100, 100, 120], closes=[100, 100, 100, 130])
    signal = pd.Series([0, 1, 1, 1], dtype=float)
    res = run_backtest(frame, signal, initial_capital=1000, fee_bps=0, slippage_bps=0)
    assert res["trades"][-1]["forced_exit"] is True
    assert res["metrics"]["final_equity"] == pytest.approx(1300.0)


def test_no_lookahead_future_prices_do_not_change_past_trades():
    frame = demo_ohlcv(bars=600)
    base = run_ema_backtest(frame.copy())
    tampered = frame.copy()
    tampered.loc[500:, ["open", "high", "low", "close"]] *= 3.0
    alt = run_ema_backtest(tampered)
    cutoff = frame["timestamp"].iloc[499].isoformat()
    past = lambda r: [t for t in r["trades"] if t["exit_time"] <= cutoff]
    assert past(base) == past(alt)


def test_matches_independent_vectorised_calculation_without_costs():
    frame = demo_ohlcv(bars=800)
    res = run_ema_backtest(frame, fee_bps=0, slippage_bps=0)
    sig = ema_crossover_signal(frame, 12, 26).fillna(0.0)
    held = sig.shift(1).fillna(0.0).to_numpy()  # position held during bar i (from open)
    o, c = frame["open"].to_numpy(), frame["close"].to_numpy()
    growth = 1.0
    for i in range(1, len(frame)):
        if held[i] and not held[i - 1]:
            entry = o[i]
        if not held[i] and held[i - 1]:
            growth *= o[i] / entry
    if held[-1]:
        growth *= c[-1] / entry
    assert res["metrics"]["final_equity"] == pytest.approx(10_000 * growth, abs=0.02)


def test_sharpe_annualization_depends_on_timeframe():
    frame = demo_ohlcv(bars=600)
    h1 = run_ema_backtest(frame, timeframe="1h")["metrics"]
    d1 = run_ema_backtest(frame, timeframe="1d")["metrics"]
    assert h1["sharpe_annualized"] == pytest.approx(d1["sharpe_annualized"] * math.sqrt(24), rel=1e-3)


def test_rejects_too_few_bars():
    with pytest.raises(ValueError):
        run_ema_backtest(demo_ohlcv(bars=20), slow_period=26)
