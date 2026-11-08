from itertools import pairwise

import pytest
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_root_and_health():
    assert client.get("/").status_code == 200
    assert client.get("/health").json()["status"] == "ok"


def test_backtest_demo_flags_synthetic_data():
    resp = client.post("/backtests", json={"bars": 500, "timeframe": "1h"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["synthetic_data"] is True
    assert body["metrics"]["trades"] == len(body["trades"])
    assert body["assumptions"]["execution"] == "next_bar_open"


def test_backtest_validation():
    assert client.post("/backtests", json={"fast_period": 30, "slow_period": 20}).status_code == 400
    assert client.post("/backtests", json={"bars": 100, "slow_period": 200}).status_code == 400
    assert client.post("/backtests", json={"strategy": "rsi"}).status_code == 400
    bad = {"strategy": "london_breakout", "timeframe": "4h"}
    assert client.post("/backtests", json=bad).status_code == 400


def test_backtest_candles_for_chart():
    body = client.post("/backtests", json={"bars": 300, "include_candles": True}).json()
    candles = body["candles"]
    assert len(candles) == 300
    assert all(a["time"] < b["time"] for a, b in pairwise(candles))
    names = [o["name"] for o in body["overlays"]]
    assert names == ["EMA 12", "EMA 26"]
    slow = body["overlays"][1]["data"]
    assert len(slow) == 300 and slow[0]["time"] == candles[0]["time"]
    assert "candles" not in client.post("/backtests", json={"bars": 300}).json()


def test_routes_served_under_api_prefix_and_bare():
    assert client.get("/api/health").json()["status"] == "ok"
    assert client.post("/api/backtests", json={"bars": 300}).status_code == 200
    assert client.get("/api/docs").status_code == 200
    paths = client.get("/api/openapi.json").json()["paths"]
    assert "/api/backtests" in paths and "/backtests" not in paths


def test_legacy_fields_map_to_params():
    body = client.post("/backtests", json={"bars": 300, "fast_period": 5, "slow_period": 30}).json()
    assert body["parameters"] == {"fast_period": 5, "slow_period": 30}


def test_list_strategies():
    items = client.get("/api/strategies").json()["strategies"]
    ids = {s["id"] for s in items}
    assert {"ema_crossover", "rsi_reversal", "turtle", "london_breakout"} <= ids
    # DSL presets (video + gold trend-pullback) have fixed rules, no tunable params
    assert all(s["params"] for s in items
               if s["category"] != "video" and not s["id"].startswith("gold_trend_pullback"))
    assert sum(s["category"] == "video" for s in items) == 6


def test_every_strategy_runs_through_api():
    for s in client.get("/api/strategies").json()["strategies"]:
        resp = client.post("/api/backtests", json={"strategy": s["id"], "bars": 1500,
                                                   "include_candles": True})
        assert resp.status_code == 200, (s["id"], resp.text)
        assert resp.json()["metrics"]["trades"] == len(resp.json()["trades"])


def test_compare_runs_all_on_same_data_and_reports_errors():
    body = client.post("/api/compare", json={"bars": 1500, "timeframe": "4h"}).json()
    rows = {r["strategy"]: r for r in body["results"]}
    assert "error" in rows["london_breakout"]          # session needs <= 1h
    assert rows["ema_crossover"]["metrics"]["trades"] >= 0
    direct = client.post("/api/backtests", json={"bars": 1500, "timeframe": "4h"}).json()
    assert rows["ema_crossover"]["metrics"]["net_return_pct"] == direct["metrics"]["net_return_pct"]


RISK = {"risk_per_trade_pct": 0.5, "daily_loss_limit_pct": 3, "max_total_loss_pct": 10}


def test_backtest_with_risk_settings_uses_risk_engine():
    body = client.post("/api/backtests", json={"strategy": "turtle", "bars": 2000,
                                               "risk": RISK}).json()
    assert body["risk"]["config"]["risk_per_trade_pct"] == 0.5
    assert body["risk"]["worst_day_pct"] > -3.0001
    assert all("exit_reason" in t for t in body["trades"])
    assert "risk" not in client.post("/api/backtests", json={"bars": 500}).json()


def test_compare_with_risk_reports_rule_metrics():
    body = client.post("/api/compare", json={"bars": 2000, "risk": RISK}).json()
    ok = [r for r in body["results"] if "metrics" in r]
    assert ok and all("worst_day_pct" in r["risk"] for r in ok)


def test_custom_backtest_with_risk_uses_spec_stop_intrabar():
    spec = {"indicators": [{"id": "e", "type": "ema", "period": 50}],
            "entry": {"all": [{"left": "close", "op": "crosses_above", "right": "e"}]},
            "exit": {"all": [{"left": "close", "op": "crosses_below", "right": "e"}]},
            "stop_loss_pct": 1.5}
    body = client.post("/api/custom/backtest", json={"spec": spec, "bars": 2000,
                                                     "risk": RISK}).json()
    stops = [t for t in body["trades"] if t["exit_reason"] == "stop"]
    assert stops
    for t in stops:
        assert t["stop_price"] == pytest.approx(t["entry_price"] * 0.985, rel=1e-6)
