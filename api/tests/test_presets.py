import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

from app.services.backtester import run_backtest
from app.services.market_data import demo_ohlcv
from app.strategies import STRATEGIES, build_signal
from app.strategies.dsl import StrategySpec, _Context, custom_signal, describe
from app.strategies.library import rsi
from app.strategies.presets import PRESET_SPECS


def daily(closes, highs=None):
    c = np.asarray(closes, float)
    h = c * 1.001 if highs is None else np.asarray(highs, float)
    ts = pd.date_range("2020-01-01", periods=len(c), freq="1D", tz="UTC")
    return pd.DataFrame({"timestamp": ts, "open": c, "high": h, "low": c * 0.999, "close": c,
                         "volume": 1.0})


def test_six_video_presets_registered_and_valid():
    video = {k for k, s in STRATEGIES.items() if s.category == "video"}
    assert video == {"qs_rsi2", "qs_rsi_drop", "qs_triple_rsi", "qs_normalized_rsi",
                     "qs_rsi_momentum", "qs_rsi_pullback"}
    for k in video:
        assert describe(PRESET_SPECS[k])["entry"]


def test_normalize_and_indicator_of_indicator_values():
    frame = demo_ohlcv(bars=400, timeframe="1d")
    spec = StrategySpec.model_validate({
        "indicators": [{"id": "r", "type": "rsi", "period": 2},
                       {"id": "n", "type": "normalize", "period": 50, "of": "r"},
                       {"id": "hi", "type": "highest", "period": 100, "of": "r"},
                       {"id": "lo", "type": "lowest", "period": 100, "of": "r"}],
        "entry": {"all": [{"left": "n", "op": "<", "right": "0.15"}]}, "max_bars_in_trade": 1,
    })
    ctx = _Context(spec, frame)
    r = rsi(frame["close"], 2)
    lo50, hi50 = r.rolling(50).min(), r.rolling(50).max()
    expected = (r - lo50) / (hi50 - lo50)
    got = ctx.operand("n")
    mask = expected.notna()
    np.testing.assert_allclose(got[mask], expected[mask])
    assert got[mask].between(0, 1).all()
    np.testing.assert_allclose(ctx.operand("hi").dropna(), r.rolling(100).max().dropna())
    np.testing.assert_allclose(ctx.operand("lo").dropna(), r.rolling(100).min().dropna())
    assert spec.warmup() == 2 + 1 + 100 + 1    # rsi chain + rolling window


def test_of_must_reference_earlier_indicator():
    with pytest.raises(ValidationError):
        StrategySpec.model_validate({
            "indicators": [{"id": "n", "type": "normalize", "period": 50, "of": "r"},
                           {"id": "r", "type": "rsi", "period": 2}],
            "entry": {"all": [{"left": "n", "op": "<", "right": "0.1"}]}, "max_bars_in_trade": 1})
    with pytest.raises(ValidationError):
        StrategySpec.model_validate({
            "indicators": [{"id": "r", "type": "rsi", "period": 2},
                           {"id": "x", "type": "atr", "period": 5, "of": "r"}],
            "entry": {"all": [{"left": "x", "op": "<", "right": "1"}]}, "max_bars_in_trade": 1})


def test_qs_exit_and_ten_day_time_stop_in_pullback():
    # Uptrend for SMA100, then a sharp 3-day dip (RSI5 < 30, close < SMA5).
    up = list(np.linspace(100, 130, 120))
    dip = [128, 125, 122]
    flat_down = [121.9 - 0.01 * i for i in range(12)]        # never closes above prior high
    frame = daily(up + dip + flat_down)
    frame["high"] = frame["close"] * 1.001
    sig = build_signal("qs_rsi_pullback", frame, {}, "1d")
    entry = int(np.argmax(sig.to_numpy() == 1))
    assert entry >= 120
    held = sig.iloc[entry:].to_numpy()
    # long for exactly 10 bars after the fill (fill = entry + 1), then flat
    assert (held[:10] == 1).all() and held[10] == 0


def test_qs_exit_fires_on_close_above_prior_high():
    up = list(np.linspace(100, 130, 120))
    frame = daily(up + [128, 125, 122, 126])
    sig = build_signal("qs_rsi_pullback", frame, {}, "1d")
    assert sig.iloc[122] == 1 and sig.iloc[123] == 0    # 126 > 122 * 1.001


def test_rsi_momentum_needs_both_conditions_to_fail_before_exit():
    frame = demo_ohlcv(bars=1500, timeframe="1d")
    spec = PRESET_SPECS["qs_rsi_momentum"]
    ctx = _Context(spec, frame)
    lo, hi = ctx.operand("rsi_lo"), ctx.operand("rsi_hi")
    sig = custom_signal(spec, frame).to_numpy()
    for i in range(1, len(sig)):
        if sig[i - 1] == 1 and sig[i] == 0:
            assert lo[i] <= 40 and hi[i] <= 70
        if sig[i - 1] == 0 and sig[i] == 1:
            assert lo[i] > 40 and hi[i] > 70


@pytest.mark.parametrize("strategy_id", sorted(PRESET_SPECS))
def test_presets_run_and_never_look_ahead(strategy_id):
    frame = demo_ohlcv(bars=2500, timeframe="1d")
    base = build_signal(strategy_id, frame, {}, "1d")
    tampered = frame.copy()
    tampered.loc[2001:, ["open", "high", "low", "close"]] *= 0.7
    pd.testing.assert_series_equal(base.iloc[:2001],
                                   build_signal(strategy_id, tampered, {}, "1d").iloc[:2001])
    res = run_backtest(frame, base, "1d")
    assert res["metrics"]["trades"] == len(res["trades"])
