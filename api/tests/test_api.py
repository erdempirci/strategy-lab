from itertools import pairwise

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
    assert all(s["params"] for s in items)


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
