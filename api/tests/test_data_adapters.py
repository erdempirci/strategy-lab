import httpx
import pandas as pd
import pytest

from app.data_adapters.binance import BinanceDataError, fetch_binance_ohlcv, to_binance_symbol
from app.data_adapters.mt5_csv import parse_mt5_csv
from app.data_adapters.normalize import quality_report

HOUR = 3_600_000
START = int(pd.Timestamp("2024-01-01", tz="UTC").timestamp() * 1000)


def kline(i):
    t = START + i * HOUR
    p = 100 + i
    return [t, str(p), str(p + 2), str(p - 1), str(p + 1), "10", t + HOUR - 1, "0", 1, "0", "0", "0"]


def fake_binance(total=2500):
    calls = []

    def handler(request: httpx.Request):
        calls.append(dict(request.url.params))
        end = int(request.url.params["endTime"])
        limit = int(request.url.params["limit"])
        eligible = [k for k in (kline(i) for i in range(total)) if k[0] <= end]
        return httpx.Response(200, json=eligible[-limit:])

    return httpx.Client(base_url="https://x", transport=httpx.MockTransport(handler)), calls


def test_symbol_mapping():
    assert to_binance_symbol("btc/usdt") == "BTCUSDT"
    with pytest.raises(ValueError):
        to_binance_symbol("B/T")


def test_binance_paginates_and_drops_forming_candle():
    client, calls = fake_binance(total=2500)
    # "now" is 30 minutes into candle #2499, which is therefore still forming.
    now = START + 2499 * HOUR + 30 * 60_000
    frame = fetch_binance_ohlcv("BTC/USDT", "1h", bars=1500, client=client, now_ms=now)
    assert len(frame) == 1500
    assert len(calls) == 2
    assert frame["timestamp"].iloc[-1] == pd.Timestamp(START + 2498 * HOUR, unit="ms", tz="UTC")
    assert frame["timestamp"].is_monotonic_increasing
    assert frame["close"].iloc[-1] == 100 + 2498 + 1
    assert quality_report(frame, "1h")["gaps"] == 0


def test_binance_http_error_raises():
    client = httpx.Client(base_url="https://x",
                          transport=httpx.MockTransport(lambda r: httpx.Response(451)))
    with pytest.raises(BinanceDataError):
        fetch_binance_ohlcv("BTC/USDT", "1h", bars=10, client=client, now_ms=START)


MT5_SAMPLE = (
    "<DATE>\t<TIME>\t<OPEN>\t<HIGH>\t<LOW>\t<CLOSE>\t<TICKVOL>\t<VOL>\t<SPREAD>\n"
    "2024.01.02\t02:00:00\t1.10420\t1.10480\t1.10390\t1.10450\t1234\t0\t7\n"
    "2024.01.02\t03:00:00\t1.10450\t1.10500\t1.10400\t1.10470\t1100\t0\t6\n"
    "2024.01.02\t05:00:00\t1.10470\t1.10520\t1.10460\t1.10510\t900\t0\t8\n"
)


def test_mt5_csv_converts_server_time_to_utc_and_spread():
    frame = parse_mt5_csv(MT5_SAMPLE, server_utc_offset_hours=2, point=0.00001)
    assert frame["timestamp"].iloc[0] == pd.Timestamp("2024-01-02 00:00", tz="UTC")
    assert frame["volume"].iloc[0] == 1234
    assert frame["spread"].iloc[0] == pytest.approx(0.00007)
    report = quality_report(frame, "1h")
    assert report["gaps"] == 1 and report["missing_bars_estimate"] == 1
