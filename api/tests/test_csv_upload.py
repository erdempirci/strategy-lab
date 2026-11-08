import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.data_adapters.csv_upload import CSVDataError, parse_ohlcv_csv
from app.data_adapters.markets import market_of
from app.main import app
from app.services.market_data import demo_ohlcv

client = TestClient(app)


def tv_csv(frame, iso=False, extra=True):
    """TradingView 'Export chart data' layout."""
    out = pd.DataFrame({
        "time": (frame["timestamp"].dt.strftime("%Y-%m-%dT%H:%M:%S+03:00") if iso
                 else frame["timestamp"].astype("int64") // 10**9),
        "open": frame["open"], "high": frame["high"], "low": frame["low"],
        "close": frame["close"], "Volume": frame["volume"]})
    if extra:
        out["EMA"] = np.nan
        out["Plot"] = 1.0
    return out.to_csv(index=False)


def test_tradingview_unix_export():
    src = demo_ohlcv("XAUUSD", "1h", 600)
    frame, tf = parse_ohlcv_csv(tv_csv(src))
    assert tf == "1h" and len(frame) == 600
    pd.testing.assert_series_equal(frame["close"], src["close"], check_names=False)
    assert str(frame["timestamp"].dt.tz) == "UTC"


def test_tradingview_iso_export_converts_offset_to_utc():
    src = demo_ohlcv("XAUUSD", "4h", 300)
    frame, tf = parse_ohlcv_csv(tv_csv(src, iso=True))
    assert tf == "4h"
    # "+03:00" local times are 3 hours ahead of UTC
    assert frame["timestamp"].iloc[0] == src["timestamp"].iloc[0] - pd.Timedelta(hours=3)


def test_generic_and_mt5_style_headers():
    text = ("Date;Open;High;Low;Close\n2024-01-02;10;11;9;10.5\n2024-01-03;10.5;12;10;11\n"
            "2024-01-04;11;11.5;10.2;10.8\n")
    frame, tf = parse_ohlcv_csv(text)
    assert tf == "1d" and frame["volume"].eq(0).all()
    mt5 = ("<DATE>\t<TIME>\t<OPEN>\t<HIGH>\t<LOW>\t<CLOSE>\t<TICKVOL>\n"
           "2024.01.02\t00:00:00\t1\t2\t0.5\t1.5\t10\n2024.01.02\t00:15:00\t1.5\t2\t1\t1.8\t12\n")
    frame, tf = parse_ohlcv_csv(mt5)
    assert tf == "15m" and frame["volume"].tolist() == [10, 12]


@pytest.mark.parametrize("bad", ["", "a,b\n1,2\n", "time,open,high,low\n1,2,3,4\n",
                                 "time,open,high,low,close\nnope,1,1,1,1\nnope2,1,1,1,1\n",
                                 "time,open,high,low,close\n1704067200,1,1,1,0\n1704070800,1,1,1,1\n"])
def test_bad_csv_rejected(bad):
    with pytest.raises(CSVDataError):
        parse_ohlcv_csv(bad)


def test_csv_backtest_end_to_end_with_session_calendar():
    src = demo_ohlcv("XAUUSD", "1h", 3000)
    weekday = src[src["timestamp"].dt.weekday < 5].reset_index(drop=True)
    text = tv_csv(weekday)
    info = client.post("/api/csv/inspect", json={"csv_data": text}).json()
    assert info["timeframe"] == "1h" and info["session_market"] is True
    assert info["quality"]["gaps"] == 0              # weekends are expected
    req = {"data_source": "csv", "csv_data": text, "timeframe": "1h", "bars": 50_000,
           "strategy": "ema_crossover", "symbol": "XAUUSD", "include_candles": True}
    body = client.post("/api/backtests", json=req).json()
    assert body["data_source"] == "csv_upload" and body["data_quality"]["bars"] == len(weekday)
    assert body["metrics"]["annualization_periods"] == pytest.approx(252 * 24)
    wrong = client.post("/api/backtests", json={**req, "timeframe": "4h"})
    assert wrong.status_code == 400 and "1h" in wrong.json()["detail"]
    missing = client.post("/api/backtests", json={**req, "csv_data": None})
    assert missing.status_code == 400


def test_chart_window_is_capped_but_metrics_use_all_bars():
    from app.main import CHART_BARS

    src = demo_ohlcv("BTC/USDT", "1h", CHART_BARS + 2000)
    req = {"data_source": "csv", "csv_data": tv_csv(src, extra=False), "timeframe": "1h",
           "bars": 50_000, "include_candles": True}
    body = client.post("/api/backtests", json=req).json()
    assert len(body["candles"]) == CHART_BARS
    assert body["data_quality"]["bars"] == CHART_BARS + 2000
    first = body["candles"][0]["time"]
    assert all(p["time"] >= first for o in body["overlays"] for p in o["data"])
    assert body["metrics"]["annualization_periods"] == pytest.approx(365 * 24)   # 24/7


def test_gold_market_aliases():
    for s in ("XAUUSD", "xau", "GOLD", "altın".upper().replace("İ", "I"), "GC"):
        m = market_of(s)
        assert m is not None and m.ticker == "GC=F", s
