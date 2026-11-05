import numpy as np
import pandas as pd
import pytest
from pydantic import ValidationError

from app.services.backtester import run_backtest
from app.services.market_data import demo_ohlcv
from app.strategies import build_signal
from app.strategies.dsl import StrategySpec, custom_signal, describe


def frame_from(opens, closes=None):
    opens = np.asarray(opens, float)
    closes = opens if closes is None else np.asarray(closes, float)
    ts = pd.date_range("2024-01-01", periods=len(opens), freq="1h", tz="UTC")
    return pd.DataFrame({"timestamp": ts, "open": opens, "high": np.maximum(opens, closes),
                         "low": np.minimum(opens, closes), "close": closes, "volume": 1.0})


RICH = {
    "name": "rich",
    "indicators": [
        {"id": "ema200", "type": "ema", "period": 200},
        {"id": "r", "type": "rsi", "period": 14},
        {"id": "m", "type": "macd"},
        {"id": "bb", "type": "bb", "period": 20, "std": 2},
        {"id": "hh", "type": "highest", "period": 20},
        {"id": "a", "type": "atr", "period": 14},
    ],
    "entry": {"all": [{"left": "close", "op": ">", "right": "ema200"},
                      {"left": "r", "op": "crosses_above", "right": "30"}],
              "any": [{"left": "m.hist", "op": ">", "right": "0"},
                      {"left": "close", "op": ">", "right": "hh[1]"}]},
    "exit": {"any": [{"left": "r", "op": ">=", "right": "70"},
                     {"left": "close", "op": "<", "right": "bb.lower"}]},
    "stop_loss_pct": 2, "take_profit_pct": 6, "max_bars_in_trade": 48,
}


def test_no_lookahead_rich_spec():
    spec = StrategySpec.model_validate(RICH)
    frame = demo_ohlcv(bars=1500)
    base = custom_signal(spec, frame)
    tampered = frame.copy()
    tampered.loc[1101:, ["open", "high", "low", "close"]] *= 0.6
    alt = custom_signal(spec, tampered)
    pd.testing.assert_series_equal(base.iloc[:1101], alt.iloc[:1101])
    assert set(base.dropna().unique()) <= {0.0, 1.0}


def test_dsl_ema_cross_equals_builtin_strategy():
    spec = StrategySpec.model_validate({
        "indicators": [{"id": "f", "type": "ema", "period": 12},
                       {"id": "s", "type": "ema", "period": 26}],
        "entry": {"all": [{"left": "f", "op": ">", "right": "s"}]},
        "exit": {"all": [{"left": "f", "op": "<=", "right": "s"}]},
    })
    frame = demo_ohlcv(bars=1200)
    custom = run_backtest(frame, custom_signal(spec, frame))
    builtin = run_backtest(frame, build_signal("ema_crossover", frame,
                                               {"fast_period": 12, "slow_period": 26}, "1h"))
    assert custom["trades"] == builtin["trades"]
    assert custom["metrics"]["final_equity"] == builtin["metrics"]["final_equity"]


def test_crosses_above_fires_only_on_the_crossing_bar():
    closes = [10, 10, 10, 12, 13, 14, 9, 9, 12, 13]
    spec = StrategySpec.model_validate({
        "entry": {"all": [{"left": "close", "op": "crosses_above", "right": "11"}]},
        "max_bars_in_trade": 1,
    })
    sig = custom_signal(spec, frame_from(closes))
    # Cross at bar 3 -> long; filled at bar 4 open; 1-bar limit -> exit decided at bar 4.
    assert sig[3] == 1 and sig[4] == 0
    assert sig[5] == 0                  # 14 > 11 but no fresh cross
    assert sig[8] == 1                  # crosses again after dipping to 9


def test_close_based_stop_uses_actual_fill_price():
    # Enter signal at bar 1 (close > 100), fill at bar 2 open = 100.
    opens = [100, 100, 100, 99, 97.9, 97, 96]
    spec = StrategySpec.model_validate({
        "entry": {"all": [{"left": "close", "op": ">=", "right": "100"}]},
        "stop_loss_pct": 2,
    })
    sig = custom_signal(spec, frame_from(opens))
    assert sig[1] == 1 and sig[2] == 1 and sig[3] == 1
    assert sig[4] == 0                  # 97.9 <= 100 * 0.98
    res = run_backtest(frame_from(opens), sig, fee_bps=0, slippage_bps=0)
    assert res["trades"][0]["entry_price"] == 100 and res["trades"][0]["exit_price"] == 97


def test_take_profit_and_hour_filter():
    ts_closes = [100] * 30
    frame = frame_from(ts_closes)
    frame.loc[10:, ["open", "close", "high"]] = 110.0
    spec = StrategySpec.model_validate({
        "entry": {"all": [{"left": "hour", "op": ">=", "right": "8"},
                          {"left": "hour", "op": "<", "right": "9"}]},
        "take_profit_pct": 5,
    })
    sig = custom_signal(spec, frame)
    assert sig[7] == 0 and sig[8] == 1      # 08:00 bar
    assert sig[10] == 0                     # 110 >= 100 * 1.05


@pytest.mark.parametrize("bad", [
    {"entry": {"all": [{"left": "close", "op": ">", "right": "ema9"}]}, "stop_loss_pct": 1},
    {"entry": {"all": [{"left": "close[-1]", "op": ">", "right": "1"}]}, "stop_loss_pct": 1},
    {"entry": {"all": [{"left": "__import__('os')", "op": ">", "right": "1"}]}, "stop_loss_pct": 1},
    {"indicators": [{"id": "m", "type": "macd"}],
     "entry": {"all": [{"left": "m", "op": ">", "right": "0"}]}, "stop_loss_pct": 1},
    {"entry": {"all": [{"left": "close", "op": ">", "right": "1"}]}},           # no exit
    {"entry": {"all": []}, "stop_loss_pct": 1},                                  # no entry
    {"indicators": [{"id": "close", "type": "sma"}],
     "entry": {"all": [{"left": "close", "op": ">", "right": "1"}]}, "stop_loss_pct": 1},
    {"entry": {"all": [{"left": "close", "op": "==", "right": "1"}]}, "stop_loss_pct": 1},
])
def test_invalid_specs_rejected(bad):
    with pytest.raises(ValidationError):
        StrategySpec.model_validate(bad)


def test_describe_is_turkish_and_complete():
    text = describe(StrategySpec.model_validate(RICH))
    assert any("yukarı keser" in line for line in text["entry"])
    assert any("1 bar önce" in line for line in text["entry"])
    assert len(text["risk"]) == 3
