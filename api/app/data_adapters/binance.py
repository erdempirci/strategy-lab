"""Binance spot OHLCV adapter (public market data, no API key).

Default host is `data-api.binance.vision`, Binance's public market-data-only
endpoint. `api.binance.com` rejects requests from some regions (HTTP 451 for
US IPs, which includes Vercel's default `iad1` region). Override with the
BINANCE_BASE_URL environment variable.

Only CLOSED candles are returned: the still-forming last candle is dropped so a
backtest never sees a bar whose close is not final yet.
"""

from __future__ import annotations

import os
import time

import httpx
import pandas as pd

from app.data_adapters.normalize import normalize_ohlcv
from app.services.timeframes import timeframe_minutes

DEFAULT_BASE_URL = "https://data-api.binance.vision"
MAX_LIMIT = 1000
VALID_INTERVALS = {
    "1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d", "3d", "1w",
}


class BinanceDataError(RuntimeError):
    pass


def to_binance_symbol(symbol: str) -> str:
    """'BTC/USDT' or 'btc-usdt' -> 'BTCUSDT'."""
    cleaned = symbol.replace("/", "").replace("-", "").replace("_", "").upper()
    if not cleaned.isalnum() or len(cleaned) < 5:
        raise ValueError(f"Invalid Binance symbol: {symbol!r}")
    return cleaned


def _get(client: httpx.Client, params: dict, retries: int = 3) -> list:
    for attempt in range(retries):
        resp = client.get("/api/v3/klines", params=params)
        if resp.status_code in (418, 429) and attempt < retries - 1:
            time.sleep(float(resp.headers.get("Retry-After", 2 ** attempt)))
            continue
        if resp.status_code == 451:
            raise BinanceDataError("Binance blocked this region (HTTP 451); set BINANCE_BASE_URL")
        if resp.status_code != 200:
            raise BinanceDataError(f"Binance HTTP {resp.status_code}: {resp.text[:200]}")
        return resp.json()
    raise BinanceDataError("Binance rate limit: retries exhausted")


def fetch_binance_ohlcv(
    symbol: str,
    timeframe: str,
    bars: int = 1000,
    end: pd.Timestamp | None = None,
    client: httpx.Client | None = None,
    now_ms: int | None = None,
) -> pd.DataFrame:
    """Fetch the most recent `bars` closed candles ending at `end` (default: now)."""
    if timeframe not in VALID_INTERVALS:
        raise ValueError(f"Unsupported Binance interval {timeframe!r}")
    if bars < 1 or bars > 20_000:
        raise ValueError("bars must be between 1 and 20000")
    pair = to_binance_symbol(symbol)
    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    end_ms = int(pd.Timestamp(end).timestamp() * 1000) if end is not None else now_ms
    step_ms = timeframe_minutes(timeframe) * 60_000

    own_client = client is None
    client = client or httpx.Client(
        base_url=os.getenv("BINANCE_BASE_URL", DEFAULT_BASE_URL), timeout=15.0
    )
    rows: list[list] = []
    try:
        cursor = end_ms
        # +1 so we can still return `bars` after dropping a forming candle.
        while len(rows) < bars + 1:
            limit = min(MAX_LIMIT, bars + 1 - len(rows))
            batch = _get(client, {"symbol": pair, "interval": timeframe,
                                  "endTime": cursor, "limit": limit})
            if not batch:
                break
            rows = batch + rows
            cursor = int(batch[0][0]) - 1
            if len(batch) < limit:
                break
    finally:
        if own_client:
            client.close()

    if not rows:
        raise BinanceDataError(f"No data returned for {pair} {timeframe}")
    raw = pd.DataFrame(rows).iloc[:, :7]
    raw.columns = ["open_time", "open", "high", "low", "close", "volume", "close_time"]
    raw = raw.drop_duplicates("open_time")
    raw = raw[raw["close_time"].astype("int64") < min(now_ms, end_ms + step_ms)]
    raw["timestamp"] = pd.to_datetime(raw["open_time"].astype("int64"), unit="ms", utc=True)
    frame = normalize_ohlcv(raw)
    return frame.tail(bars).reset_index(drop=True)
