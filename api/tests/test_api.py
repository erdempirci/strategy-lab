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
    assert client.post("/backtests", json={"fast_period": 30, "slow_period": 20}).status_code == 422
    assert client.post("/backtests", json={"bars": 100, "slow_period": 200}).status_code == 422
    assert client.post("/backtests", json={"strategy": "rsi"}).status_code == 400


def test_backtest_candles_for_chart():
    body = client.post("/backtests", json={"bars": 300, "include_candles": True}).json()
    candles = body["candles"]
    assert len(candles) == 300
    assert candles[0]["ema_slow"] is None and candles[-1]["ema_slow"] is not None
    assert all(a["time"] < b["time"] for a, b in pairwise(candles))
    assert "candles" not in client.post("/backtests", json={"bars": 300}).json()


def test_routes_served_under_api_prefix_and_bare():
    assert client.get("/api/health").json()["status"] == "ok"
    assert client.post("/api/backtests", json={"bars": 300}).status_code == 200
    assert client.get("/api/docs").status_code == 200
    paths = client.get("/api/openapi.json").json()["paths"]
    assert "/api/backtests" in paths and "/backtests" not in paths
