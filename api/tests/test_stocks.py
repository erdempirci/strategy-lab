import math
from datetime import UTC, datetime

import httpx
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app import main
from app.data_adapters.normalize import quality_report
from app.data_adapters.stocks import StockDataError, fetch_stock_daily

# Mon 2024-01-08 .. Fri 2024-01-12 sessions; bars stamped 14:30 UTC (09:30 New York).
DAYS = ["2024-01-08", "2024-01-09", "2024-01-10", "2024-01-11", "2024-01-12"]
TS = [int(pd.Timestamp(f"{d} 14:30", tz="UTC").timestamp()) for d in DAYS]


def yahoo_body(adj_factor=0.5):
    closes = [100.0, 101.0, 102.0, 103.0, 104.0]
    return {"chart": {"result": [{
        "meta": {"exchangeTimezoneName": "America/New_York"},
        "timestamp": TS,
        "indicators": {"quote": [{"open": [c - 1 for c in closes], "high": [c + 1 for c in closes],
                                  "low": [c - 2 for c in closes], "close": closes,
                                  "volume": [1000] * 5}],
                       "adjclose": [{"adjclose": [c * adj_factor for c in closes]}]}}]}}


STOOQ_CSV = "Date,Open,High,Low,Close,Volume\n" + "\n".join(
    f"{d},{99 + i},{101 + i},{98 + i},{100 + i},500" for i, d in enumerate(DAYS))


def client(yahoo=None, stooq=None, seen=None):
    def handler(req: httpx.Request):
        if seen is not None:
            seen.append(req.url.host)
        if "yahoo" in req.url.host:
            return yahoo(req) if yahoo else httpx.Response(429)
        return stooq(req) if stooq else httpx.Response(404)
    return httpx.Client(transport=httpx.MockTransport(handler))


AFTER_CLOSE = datetime(2024, 1, 12, 23, 0, tzinfo=UTC)      # 18:00 New York
DURING_SESSION = datetime(2024, 1, 12, 17, 0, tzinfo=UTC)   # 12:00 New York


def test_yahoo_adjusted_prices_and_session_dates():
    c = client(yahoo=lambda r: httpx.Response(200, json=yahoo_body(0.5)))
    frame, label = fetch_stock_daily("spy", client=c, now=AFTER_CLOSE)
    assert label == "yahoo_daily_adjusted" and len(frame) == 5
    assert frame["timestamp"].iloc[0] == pd.Timestamp("2024-01-08", tz="UTC")
    assert frame["close"].iloc[0] == pytest.approx(50.0)       # adjusted
    assert frame["open"].iloc[0] == pytest.approx(49.5)        # same factor applied to OHLC
    assert frame["high"].iloc[-1] == pytest.approx(52.5)


def test_drops_todays_unfinished_session():
    c = client(yahoo=lambda r: httpx.Response(200, json=yahoo_body()))
    frame, _ = fetch_stock_daily("SPY", client=c, now=DURING_SESSION)
    assert frame["timestamp"].iloc[-1] == pd.Timestamp("2024-01-11", tz="UTC")


def test_falls_back_to_stooq():
    seen = []
    c = client(stooq=lambda r: httpx.Response(200, text=STOOQ_CSV), seen=seen)
    frame, label = fetch_stock_daily("SPY", client=c, now=AFTER_CLOSE)
    assert label == "stooq_daily" and len(frame) == 5
    assert seen == ["query1.finance.yahoo.com", "stooq.com"]


def test_both_sources_fail_and_daily_only():
    with pytest.raises(StockDataError):
        fetch_stock_daily("SPY", client=client(), now=AFTER_CLOSE)
    with pytest.raises(ValueError):
        fetch_stock_daily("SPY", timeframe="1h", client=client())
    with pytest.raises(ValueError):
        fetch_stock_daily("SPY;DROP", client=client())


def test_weekends_are_not_reported_as_gaps():
    ts = pd.bdate_range("2024-01-01", periods=30, tz="UTC")
    frame = pd.DataFrame({"timestamp": ts, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0,
                          "volume": 1.0})
    assert quality_report(frame, "1d")["gaps"] > 0
    assert quality_report(frame, "1d", pd.Timedelta(days=4))["gaps"] == 0


def test_stock_backtest_uses_252_day_calendar(monkeypatch):
    from app.services.market_data import demo_ohlcv

    frame = demo_ohlcv("SPY", "1d", 1500)
    from app.data_adapters import stocks
    monkeypatch.setattr(stocks, "fetch_stock_daily", lambda s, t, b: (frame, "yahoo_daily_adjusted"))
    import app.services.market_data as md
    monkeypatch.setattr(md, "demo_ohlcv", lambda **kw: frame)
    api = TestClient(main.app)
    req = {"strategy": "qs_rsi2", "timeframe": "1d", "bars": 1500, "symbol": "SPY"}
    stock = api.post("/api/backtests", json={**req, "data_source": "yahoo"}).json()
    demo = api.post("/api/backtests", json={**req, "data_source": "demo"}).json()
    assert stock["data_source"] == "yahoo_daily_adjusted"
    assert stock["metrics"]["trades"] == demo["metrics"]["trades"]
    ratio = stock["metrics"]["sharpe_annualized"] / demo["metrics"]["sharpe_annualized"]
    assert ratio == pytest.approx(math.sqrt(252 / 365), rel=1e-3)
