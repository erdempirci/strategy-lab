import numpy as np
import pandas as pd
import pytest

from app.services.market_data import demo_ohlcv
from app.strategies import STRATEGIES, build_signal
from app.strategies.library import rsi


def frame_from_close(closes, freq="1h", start="2024-01-01"):
    closes = np.asarray(closes, dtype=float)
    ts = pd.date_range(start, periods=len(closes), freq=freq, tz="UTC")
    return pd.DataFrame({"timestamp": ts, "open": closes, "high": closes * 1.001,
                         "low": closes * 0.999, "close": closes, "volume": 1.0})


@pytest.mark.parametrize("strategy_id", sorted(STRATEGIES))
def test_no_lookahead_for_every_strategy(strategy_id):
    """Changing bars after `cut` must not change any signal at or before `cut`."""
    spec = STRATEGIES[strategy_id]
    frame = demo_ohlcv(bars=1500)
    params = spec.resolve({}, "1h")
    base = build_signal(strategy_id, frame, params, "1h")
    tampered = frame.copy()
    cut = 1100
    tampered.loc[cut + 1:, ["open", "high", "low", "close"]] *= 1.7
    alt = build_signal(strategy_id, tampered, params, "1h")
    pd.testing.assert_series_equal(base.iloc[: cut + 1], alt.iloc[: cut + 1])
    assert len(base) == len(frame)
    assert set(base.dropna().unique()) <= {0.0, 1.0}
    assert base.notna().sum() > 0


def test_rsi_matches_hand_calculated_wilder():
    close = pd.Series([10, 11, 12, 11, 13, 12, 14], dtype=float)
    period = 3
    delta = close.diff()
    gains, losses = delta.clip(lower=0), -delta.clip(upper=0)
    ag = al = 0.0
    expected = []
    for i in range(len(close)):
        if i == 0:
            expected.append(np.nan)
            continue
        ag = ag + (gains[i] - ag) / period if i > 1 else gains[i]
        al = al + (losses[i] - al) / period if i > 1 else losses[i]
        expected.append(100 - 100 / (1 + ag / al) if al else 100.0)
    got = rsi(close, period)
    assert got.iloc[:period].isna().all()
    np.testing.assert_allclose(got.iloc[period:], expected[period:], rtol=1e-9)


def test_turtle_enters_on_channel_break_and_exits_on_low_break():
    closes = [100] * 25 + [101, 102, 103] + [99] + [100] * 5
    frame = frame_from_close(closes)
    sig = build_signal("turtle", frame, {"entry_period": 20, "exit_period": 3}, "1h")
    assert sig[24] == 0 and sig[25] == 1 and sig[27] == 1
    assert sig[28] == 0  # 99 closes below the prior 3-bar low


def test_bollinger_breakout_latch():
    closes = [100, 100.5] * 15 + [110, 108, 106, 99, 99]
    frame = frame_from_close(closes)
    sig = build_signal("bollinger_breakout", frame, {"period": 20, "std_mult": 2}, "1h")
    assert sig[29] == 0
    assert sig[30] == 1 and sig[31] == 1  # stays long above the middle band
    assert sig[33] == 0


def test_london_breakout_session_logic():
    # Two UTC days of hourly bars. Range 00-07 high = 101 (scaled by 1.001).
    day = [100] * 7 + [100, 102, 103, 103, 103, 103, 103, 103, 103, 103] + [100] * 7
    frame = frame_from_close(day * 2)
    sig = build_signal("london_breakout", frame,
                       {"range_start": 0, "range_end": 7, "exit_hour": 16}, "1h")
    first = sig[:24]
    assert (first[:8] == 0).all()           # inside range and the 07:00 bar at range
    assert first[8] == 1                    # 08:00 bar closes at 102 > range high
    assert (first[8:15] == 1).all()
    assert first[15] == 0                   # 15:00 bar ends at 16:00 -> flat
    assert (first[16:] == 0).all()          # one trade per day, no re-entry
    assert sig[24 + 8] == 1                 # next day trades again


def test_param_validation():
    with pytest.raises(ValueError):
        STRATEGIES["ema_crossover"].resolve({"fast_period": 50, "slow_period": 20})
    with pytest.raises(ValueError):
        STRATEGIES["ema_crossover"].resolve({"nope": 1})
    with pytest.raises(ValueError):
        STRATEGIES["rsi_reversal"].resolve({"period": 1})
    with pytest.raises(ValueError):
        STRATEGIES["london_breakout"].resolve({}, "4h")
    assert STRATEGIES["bollinger_breakout"].resolve({"std_mult": 2.5})["std_mult"] == 2.5
