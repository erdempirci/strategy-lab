"""Non-crypto markets the app knows by name, and how to load any symbol.

Crypto pairs (BTC/USDT …) go to Binance. Named index futures go to Yahoo.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

SESSION_GAP = pd.Timedelta(hours=80)
FEED_DELAY = pd.Timedelta(minutes=15)


@dataclass(frozen=True)
class Market:
    symbol: str
    ticker: str
    label: str
    fee_bps: float
    slippage_bps: float
    # Session market: closed on weekends and during the daily break, so gaps up to
    # this length are expected and are not data problems.
    expected_gap: pd.Timedelta = SESSION_GAP
    delay: pd.Timedelta = FEED_DELAY


MARKETS = {
    "US100": Market("US100", "NQ=F", "US100 · Nasdaq 100 vadeli (NQ, Yahoo)", 0.5, 0.5),
    "US500": Market("US500", "ES=F", "US500 · S&P 500 vadeli (ES, Yahoo)", 0.5, 0.5),
}
ALIASES = {"NAS100": "US100", "NQ": "US100", "NDX": "US100", "USTEC": "US100",
           "SPX500": "US500", "ES": "US500", "SPX": "US500", "US500": "US500", "US100": "US100"}


def market_of(symbol: str) -> Market | None:
    key = ALIASES.get(symbol.strip().upper().replace("/", "").replace("!", ""))
    return MARKETS.get(key) if key else None


def load_ohlcv(source: str, symbol: str, timeframe: str, bars: int) -> tuple[pd.DataFrame, str]:
    """(frame, data source label). `source` is "demo" or a real-data source."""
    from app.data_adapters.binance import fetch_binance_ohlcv
    from app.data_adapters.yahoo import fetch_yahoo_ohlcv
    from app.services.market_data import demo_ohlcv

    if source == "demo":
        return demo_ohlcv(symbol=symbol, timeframe=timeframe, bars=bars), "deterministic_demo"
    m = market_of(symbol)
    if m is not None:
        return fetch_yahoo_ohlcv(m.ticker, timeframe, bars), f"yahoo_{m.symbol.lower()}_futures"
    if source == "yahoo":
        raise ValueError(f"{symbol}: Yahoo kaynağı için US100 veya US500 yazın")
    return fetch_binance_ohlcv(symbol, timeframe, bars), "binance_spot"
