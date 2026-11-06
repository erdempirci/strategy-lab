"""Yahoo Finance chart API — US index futures (US100 = NQ=F, US500 = ES=F).

Public endpoint, no API key. Continuous front-month contracts trade ~23h a day,
Sunday evening to Friday afternoon (US time), with a daily break around 21–22 UTC.

Caveats the rest of the app relies on:
- Quotes are delayed (~10 min for CME). A bar only counts as closed DELAY after its
  end, so the still-forming and the not-yet-complete bar are dropped here.
- Yahoo has no 2h/4h interval: those are resampled from 60m bars (UTC-aligned).
- 60m history is limited to ~730 days.
- The continuous contract jumps at each quarterly roll (a few tenths of a percent).
"""

from __future__ import annotations

import os
import time

import httpx
import numpy as np
import pandas as pd

from app.data_adapters.normalize import normalize_ohlcv
from app.services.timeframes import timeframe_minutes

HOSTS = ("https://query1.finance.yahoo.com", "https://query2.finance.yahoo.com")
UA = "Mozilla/5.0 (compatible; strategy-lab/0.7)"
DELAY = pd.Timedelta(minutes=15)
MAX_60M_DAYS = 729
SUPPORTED = ("1h", "2h", "4h", "1d")


class YahooDataError(RuntimeError):
    pass


def _span_days(timeframe: str, bars: int) -> float:
    # ~5 trading days a week, ~23 trading hours a day, plus holidays and margin
    return bars * timeframe_minutes(timeframe) / 1440 * (7 / 5) * (24 / 23) * 1.15 + 4


def _get(ticker: str, params: dict, client: httpx.Client) -> dict:
    hosts = [os.getenv("YAHOO_BASE_URL")] if os.getenv("YAHOO_BASE_URL") else list(HOSTS)
    last = "no response"
    for attempt in range(4):
        host = hosts[attempt % len(hosts)]
        try:
            resp = client.get(f"{host}/v8/finance/chart/{ticker}", params=params,
                              headers={"User-Agent": UA, "Accept": "application/json"})
        except httpx.HTTPError as exc:
            last = str(exc)
        else:
            if resp.status_code == 200:
                body = resp.json()
                err = (body.get("chart") or {}).get("error")
                if err:
                    raise YahooDataError(f"Yahoo: {err.get('description') or err}")
                return body
            last = f"HTTP {resp.status_code}"
            if resp.status_code not in (429, 500, 502, 503, 504):
                break
        time.sleep(0.5 * 2 ** attempt)
    raise YahooDataError(f"Yahoo verisi alınamadı ({ticker}): {last}")


def parse_chart(body: dict) -> pd.DataFrame:
    try:
        res = body["chart"]["result"][0]
        ts = res.get("timestamp") or []
        q = res["indicators"]["quote"][0]
    except (KeyError, IndexError, TypeError) as exc:
        raise YahooDataError("Yahoo yanıtı beklenen biçimde değil") from exc
    frame = pd.DataFrame({
        "timestamp": pd.to_datetime(ts, unit="s", utc=True),
        "open": q.get("open"), "high": q.get("high"), "low": q.get("low"),
        "close": q.get("close"), "volume": q.get("volume"),
    })
    frame = frame.dropna(subset=["open", "high", "low", "close"])
    frame["volume"] = frame["volume"].fillna(0)
    return frame


def _on_grid(frame: pd.DataFrame) -> pd.DataFrame:
    """Keep bars on the dominant minute (drops Yahoo's extra live-quote row)."""
    if frame.empty:
        return frame
    minute = frame["timestamp"].dt.minute
    return frame[minute == minute.mode().iloc[0]]


def resample(frame: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    rule = f"{timeframe_minutes(timeframe)}min"
    g = frame.set_index("timestamp").resample(rule, origin="epoch", label="left", closed="left")
    out = g.agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"})
    return out.dropna(subset=["open"]).reset_index()


def drop_unclosed(frame: pd.DataFrame, timeframe: str, now: pd.Timestamp,
                  delay: pd.Timedelta = DELAY) -> pd.DataFrame:
    step = pd.Timedelta(minutes=timeframe_minutes(timeframe))
    return frame[frame["timestamp"] + step + delay <= now].reset_index(drop=True)


def fetch_yahoo_ohlcv(ticker: str, timeframe: str, bars: int,
                      now: pd.Timestamp | None = None,
                      client: httpx.Client | None = None) -> pd.DataFrame:
    if timeframe not in SUPPORTED:
        raise ValueError(f"Vadeli endeks verisi için zaman dilimi {', '.join(SUPPORTED)} "
                             "olmalı (Yahoo 60 dakikadan kısa geçmişi 60 günle sınırlar).")
    now = pd.Timestamp.now(tz="UTC") if now is None else pd.Timestamp(now)
    interval = "1d" if timeframe == "1d" else "60m"
    days = _span_days(timeframe, bars)
    if interval == "60m":
        days = min(days, MAX_60M_DAYS)
    params = {"period1": int((now - pd.Timedelta(days=days)).timestamp()),
              "period2": int(now.timestamp()), "interval": interval,
              "includePrePost": "false", "events": ""}
    own = client is None
    client = client or httpx.Client(timeout=20.0, follow_redirects=True)
    try:
        frame = parse_chart(_get(ticker, params, client))
    finally:
        if own:
            client.close()
    if interval == "1d":
        frame["timestamp"] = frame["timestamp"].dt.normalize()
        frame = frame.drop_duplicates("timestamp", keep="last")
    else:
        frame = _on_grid(frame)
        if timeframe != "1h":
            frame = resample(frame, timeframe)
    frame = drop_unclosed(frame.sort_values("timestamp"), timeframe, now)
    if frame.empty:
        raise YahooDataError(f"{ticker} için kapanmış bar yok")
    frame = normalize_ohlcv(frame)
    return frame.tail(bars).reset_index(drop=True).astype(
        {c: np.float64 for c in ("open", "high", "low", "close", "volume")})
