"""Daily stock / ETF bars (e.g. SPY) — Yahoo Finance chart API, Stooq as fallback.

- Prices are dividend- and split-adjusted (Yahoo adjclose / Stooq adjusted series), which
  is what long-horizon backtests such as the RSI video use.
- One bar per trading day, timestamped at 00:00 UTC of the session date.
- Today's bar is dropped until the US close (16:00 New York + 15 min) so a backtest never
  sees an unfinished daily candle.
- Daily only.
"""

from __future__ import annotations

import io
import re
from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

import httpx
import pandas as pd

from app.data_adapters.normalize import normalize_ohlcv

YAHOO_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
STOOQ_URL = "https://stooq.com/q/d/l/"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; strategy-lab/1.0)"}
NY = ZoneInfo("America/New_York")
SYMBOL_RE = re.compile(r"^[A-Z0-9.\-^=]{1,15}$")


class StockDataError(RuntimeError):
    pass


def _clean_symbol(symbol: str) -> str:
    sym = symbol.strip().upper()
    if not SYMBOL_RE.match(sym):
        raise ValueError(f"Invalid stock symbol: {symbol!r} (e.g. SPY, QQQ, AAPL)")
    return sym


def _drop_unfinished(frame: pd.DataFrame, now: datetime) -> pd.DataFrame:
    ny_now = now.astimezone(NY)
    if ny_now.time() < time(16, 15):
        today = pd.Timestamp(ny_now.date(), tz="UTC")
        frame = frame[frame["timestamp"] < today]
    return frame


def _yahoo(client: httpx.Client, symbol: str) -> pd.DataFrame:
    resp = client.get(YAHOO_URL.format(symbol=symbol), headers=HEADERS, params={
        "period1": 0, "period2": int(datetime.now(UTC).timestamp()), "interval": "1d",
        "events": "div,split", "includeAdjustedClose": "true"})
    if resp.status_code != 200:
        raise StockDataError(f"Yahoo HTTP {resp.status_code}")
    result = (resp.json().get("chart") or {}).get("result")
    if not result:
        raise StockDataError(f"Yahoo returned no data for {symbol}")
    r = result[0]
    q = r["indicators"]["quote"][0]
    adj = (r["indicators"].get("adjclose") or [{}])[0].get("adjclose")
    raw = pd.DataFrame({"t": r["timestamp"], "open": q["open"], "high": q["high"],
                        "low": q["low"], "close": q["close"], "volume": q["volume"]})
    raw["adj"] = adj if adj is not None else raw["close"]
    raw = raw.dropna(subset=["open", "high", "low", "close", "adj"])
    factor = raw["adj"] / raw["close"]
    for col in ("open", "high", "low", "close"):
        raw[col] = raw[col] * factor
    tz = (r.get("meta") or {}).get("exchangeTimezoneName") or "America/New_York"
    local = pd.to_datetime(raw["t"], unit="s", utc=True).dt.tz_convert(tz)
    raw["timestamp"] = pd.to_datetime(local.dt.date).dt.tz_localize("UTC")
    raw["volume"] = raw["volume"].fillna(0)
    return raw


def _stooq(client: httpx.Client, symbol: str) -> pd.DataFrame:
    code = symbol.lower() if "." in symbol else f"{symbol.lower()}.us"
    resp = client.get(STOOQ_URL, headers=HEADERS, params={"s": code, "i": "d"})
    text = resp.text
    if resp.status_code != 200 or not text.lower().startswith("date"):
        raise StockDataError(f"Stooq returned no data for {symbol}")
    raw = pd.read_csv(io.StringIO(text))
    raw.columns = [c.lower() for c in raw.columns]
    raw["timestamp"] = pd.to_datetime(raw["date"]).dt.tz_localize("UTC")
    if "volume" not in raw:
        raw["volume"] = 0.0
    return raw


def fetch_stock_daily(symbol: str, timeframe: str = "1d", bars: int = 5000,
                      client: httpx.Client | None = None,
                      now: datetime | None = None) -> tuple[pd.DataFrame, str]:
    """Return (frame, provider label)."""
    if timeframe != "1d":
        raise ValueError("Hisse/ETF verisi sadece günlük (1d) zaman diliminde")
    sym = _clean_symbol(symbol)
    now = now or datetime.now(UTC)
    own = client is None
    client = client or httpx.Client(timeout=20.0, follow_redirects=True)
    errors = []
    try:
        for label, fn in (("yahoo_daily_adjusted", _yahoo), ("stooq_daily", _stooq)):
            try:
                raw = fn(client, sym)
                frame = _drop_unfinished(normalize_ohlcv(raw), now)
                if len(frame) == 0:
                    raise StockDataError("empty")
                return frame.tail(bars).reset_index(drop=True), label
            except (StockDataError, httpx.HTTPError, KeyError, ValueError) as exc:
                errors.append(f"{label}: {exc}")
    finally:
        if own:
            client.close()
    raise StockDataError(f"{sym} için veri alınamadı ({'; '.join(errors)})")
