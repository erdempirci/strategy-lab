import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.market_data import demo_ohlcv
from app.strategies import STRATEGIES
from app.strategies.dsl import StrategySpec, _Context, describe
from app.strategies.presets import PRESET_SPECS

client = TestClient(app)
GOLD = "gold_trend_pullback"


def test_gold_preset_registered_as_ready_strategy():
    s = STRATEGIES[GOLD]
    assert s.category != "video"          # listed under "Hazır stratejiler"
    spec = PRESET_SPECS[GOLD]
    assert spec.has_long and spec.has_short and spec.take_profit_rr == 1.5
    assert spec.stop_at == "lo5" and spec.short_stop_at == "hi5"
    assert any("1:1.5" in r for r in describe(spec)["risk"])


def _adx_spec():
    return StrategySpec.model_validate({
        "indicators": [{"id": "a", "type": "adx", "period": 14}],
        "entry": {"all": [{"left": "a", "op": ">", "right": "20"}]}, "max_bars_in_trade": 1})


def test_adx_high_in_trend_low_in_range():
    n = 300
    ts = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    trend = np.linspace(100, 160, n)
    rng = np.random.default_rng(1)
    chop = 100 + rng.normal(0, 0.5, n)
    adx = {}
    for name, c in (("trend", trend), ("chop", chop)):
        frame = pd.DataFrame({"timestamp": ts, "open": c, "high": c + 0.6, "low": c - 0.6,
                              "close": c, "volume": 1.0})
        adx[name] = _Context(_adx_spec(), frame).operand("a")
    assert adx["trend"].isna().iloc[:28].all() and adx["trend"].notna().iloc[28:].all()
    assert adx["trend"].iloc[-1] > 40 and adx["chop"].iloc[-1] < 25
    for s in adx.values():
        assert s.dropna().between(0, 100).all()


def test_adx_no_lookahead():
    frame = demo_ohlcv(bars=400, timeframe="1h")
    full = _Context(_adx_spec(), frame).operand("a")
    cut = _Context(_adx_spec(), frame.iloc[:250]).operand("a")
    np.testing.assert_allclose(full.iloc[:250].dropna(), cut.dropna())


@pytest.mark.parametrize("risk", [None, {"risk_per_trade_pct": 0.5}])
def test_gold_preset_backtests(risk):
    body = {"strategy": GOLD, "bars": 3000, "timeframe": "1h"}
    if risk:
        body["risk"] = risk
    resp = client.post("/api/backtests", json=body)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["metrics"]["trades"] == len(data["trades"])


def test_gold_preset_risk_mode_uses_own_stop_and_rr_target():
    data = client.post("/api/backtests", json={
        "strategy": GOLD, "bars": 3000, "timeframe": "1h",
        "risk": {"risk_per_trade_pct": 0.5}}).json()
    assert data["trades"]
    checked = 0
    for t in data["trades"]:
        if t.get("target_price") and t.get("stop_price"):
            risk = abs(t["entry_price"] - t["stop_price"])
            assert abs(t["target_price"] - t["entry_price"]) == pytest.approx(1.5 * risk, rel=1e-6)
            checked += 1
    assert checked
