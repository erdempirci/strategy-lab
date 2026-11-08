"""Indicators agree with bukosabino/ta (MIT) after warm-up. Skipped when `ta` is not installed.

RSI seeds its first average differently from `ta` (zero vs. first difference), so it is
compared after the seed has decayed (< 1e-6 after 300 bars at period 14).
"""

import pytest

from app.services.market_data import demo_ohlcv
from app.services.risk_engine import wilder_atr
from app.strategies.dsl import adx
from app.strategies.library import ema, rsi

ta = pytest.importorskip("ta")
F = demo_ohlcv(bars=1500)
C = F["close"]
SKIP = 300


def _close(a, b, tol):
    a, b = a.iloc[SKIP:], b.iloc[SKIP:]
    assert float((a - b).abs().max()) < tol


def test_ema():
    _close(ema(C, 50), ta.trend.EMAIndicator(C, 50).ema_indicator(), 1e-9)


def test_rsi():
    _close(rsi(C, 14), ta.momentum.RSIIndicator(C, 14).rsi(), 1e-6)


def test_wilder_atr():
    _close(wilder_atr(F, 14), ta.volatility.AverageTrueRange(F.high, F.low, C, 14).average_true_range(), 1e-6)


def test_adx():
    _close(adx(F, 14), ta.trend.ADXIndicator(F.high, F.low, C, 14).adx(), 1e-6)


def test_macd_and_bollinger():
    _close(ema(C, 12) - ema(C, 26), ta.trend.MACD(C, 26, 12, 9).macd(), 1e-9)
    mid, sd = C.rolling(20).mean(), C.rolling(20).std(ddof=0)
    _close(mid + 2 * sd, ta.volatility.BollingerBands(C, 20, 2).bollinger_hband(), 1e-9)
