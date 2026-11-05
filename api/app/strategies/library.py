"""Built-in long/flat strategies.

Rules every signal follows (enforced by tests/test_strategies.py):
- value at bar i uses only bars 0..i (no look-ahead);
- NaN during indicator warm-up;
- the engine fills at the next bar's open, so nothing here is shifted for execution.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.services.timeframes import timeframe_minutes
from app.strategies.base import Overlay, Param, Strategy

FAST, SLOW, TREND, BAND = "#f2b84b", "#6aa8ff", "#c084fc", "#94a3b8"


# ---------------------------------------------------------------- indicators
def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def rsi(close: pd.Series, period: int) -> pd.Series:
    """Wilder's RSI."""
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss
    out = 100 - 100 / (1 + rs)
    out[loss == 0] = 100.0
    out.iloc[:period] = np.nan
    return out


def _warm(signal: pd.Series, bars: int) -> pd.Series:
    signal = signal.astype(float)
    signal.iloc[: max(0, bars)] = np.nan
    return signal


def _latch(enter: np.ndarray, exit_: np.ndarray, valid: np.ndarray) -> pd.Series:
    """Stateful long/flat position: go long on `enter` when flat, go flat on `exit_`."""
    out = np.full(len(enter), np.nan)
    pos = 0.0
    for i in range(len(enter)):
        if not valid[i]:
            continue
        if pos == 0.0 and enter[i]:
            pos = 1.0
        elif pos == 1.0 and exit_[i]:
            pos = 0.0
        out[i] = pos
    return pd.Series(out)


# ------------------------------------------------------------------ signals
def ema_crossover(frame, fast_period, slow_period):
    close = frame["close"]
    return _warm(ema(close, fast_period) > ema(close, slow_period), slow_period - 1)


def ema_trend(frame, period, confirm_pct):
    close = frame["close"]
    line = ema(close, period)
    enter = (close > line * (1 + confirm_pct / 100)).to_numpy()
    exit_ = (close < line).to_numpy()
    valid = np.arange(len(frame)) >= period - 1
    return _latch(enter, exit_, valid)


def rsi_reversal(frame, period, oversold, exit_level):
    r = rsi(frame["close"], period)
    enter = ((r.shift(1) < oversold) & (r >= oversold)).to_numpy()
    exit_ = (r >= exit_level).to_numpy()
    return _latch(enter, exit_, r.notna().to_numpy())


def macd_trend(frame, fast_period, slow_period, signal_period, trend_period):
    close = frame["close"]
    macd = ema(close, fast_period) - ema(close, slow_period)
    long = macd > ema(macd, signal_period)
    warm = slow_period + signal_period
    if trend_period > 0:
        long &= close > ema(close, trend_period)
        warm = max(warm, trend_period - 1)
    return _warm(long, warm)


def bollinger_breakout(frame, period, std_mult):
    close = frame["close"]
    mid = close.rolling(period).mean()
    upper = mid + std_mult * close.rolling(period).std(ddof=0)
    return _latch((close > upper).to_numpy(), (close < mid).to_numpy(), mid.notna().to_numpy())


def turtle(frame, entry_period, exit_period):
    high_n = frame["high"].rolling(entry_period).max().shift(1)
    low_m = frame["low"].rolling(exit_period).min().shift(1)
    close = frame["close"]
    valid = (high_n.notna() & low_m.notna()).to_numpy()
    return _latch((close > high_n).to_numpy(), (close < low_m).to_numpy(), valid)


def session_breakout(frame, range_start, range_end, exit_hour, timeframe="1h"):
    """Long when price closes above the day's UTC session range, flat by exit_hour.

    One entry per day. Exits early if price closes back below the range low.
    """
    step = timeframe_minutes(timeframe)
    ts = pd.to_datetime(frame["timestamp"], utc=True)
    start_min = (ts.dt.hour * 60 + ts.dt.minute).to_numpy()
    end_min = start_min + step
    days = ts.dt.date.to_numpy()
    high, low, close = (frame[c].to_numpy(float) for c in ("high", "low", "close"))
    need = (range_end - range_start) * 60 // step
    rs, re, ex = range_start * 60, range_end * 60, exit_hour * 60

    out = np.full(len(frame), np.nan)
    day, r_hi, r_lo, r_count, traded, pos, seen_day_start = None, -np.inf, np.inf, 0, False, 0.0, False
    for i in range(len(frame)):
        if days[i] != day:
            day, r_hi, r_lo, r_count, traded, pos = days[i], -np.inf, np.inf, 0, False, 0.0
            seen_day_start = start_min[i] <= rs
        if rs <= start_min[i] and end_min[i] <= re:
            r_hi, r_lo, r_count = max(r_hi, high[i]), min(r_lo, low[i]), r_count + 1
        complete = seen_day_start and r_count == need
        if end_min[i] >= ex or start_min[i] < re or not complete:
            pos = 0.0
        elif pos == 0.0 and not traded and close[i] > r_hi:
            pos, traded = 1.0, True
        elif pos == 1.0 and close[i] < r_lo:
            pos = 0.0
        out[i] = pos
    return pd.Series(out)


# ----------------------------------------------------------------- overlays
def _ema_lines(frame, fast_period, slow_period, **_):
    c = frame["close"]
    return [Overlay(f"EMA {fast_period}", ema(c, fast_period), FAST),
            Overlay(f"EMA {slow_period}", ema(c, slow_period), SLOW)]


def _bollinger_lines(frame, period, std_mult, **_):
    c = frame["close"]
    mid = c.rolling(period).mean()
    sd = c.rolling(period).std(ddof=0)
    return [Overlay(f"BB üst {std_mult:g}σ", mid + std_mult * sd, BAND),
            Overlay(f"SMA {period}", mid, SLOW),
            Overlay(f"BB alt {std_mult:g}σ", mid - std_mult * sd, BAND)]


def _session_check(p, timeframe):
    if timeframe_minutes(timeframe) > 60:
        raise ValueError("Session breakout needs a timeframe of 1h or lower")
    if not (p["range_start"] < p["range_end"] < p["exit_hour"]):
        raise ValueError("range_start < range_end < exit_hour required (UTC hours)")
    if 60 % timeframe_minutes(timeframe) and timeframe_minutes(timeframe) < 60:
        raise ValueError("Timeframe must divide one hour")


def _ema_check(p, _tf):
    if p["fast_period"] >= p["slow_period"]:
        raise ValueError("fast_period must be less than slow_period")


P = Param
_session_params = lambda s, e, x: (
    P("range_start", "Range başlangıç (UTC saat)", s, 0, 22),
    P("range_end", "Range bitiş (UTC saat)", e, 1, 23),
    P("exit_hour", "Çıkış saati (UTC)", x, 2, 24),
)

STRATEGIES: dict[str, Strategy] = {s.id: s for s in [
    Strategy(
        "ema_crossover", "EMA Crossover",
        "Hızlı EMA yavaş EMA'nın üstündeyken long, altındayken nakit.",
        (P("fast_period", "Hızlı EMA", 12, 2, 200), P("slow_period", "Yavaş EMA", 26, 3, 500)),
        ema_crossover, warmup=lambda p: p["slow_period"], overlays=_ema_lines, check=_ema_check,
    ),
    Strategy(
        "ema_trend", "EMA200 Trend Following",
        "Kapanış EMA'nın belirli % üstüne çıkınca long, EMA altına inince çık.",
        (P("period", "Trend EMA", 200, 10, 500),
         P("confirm_pct", "Onay eşiği %", 0.0, 0, 10, 0.1, integer=False)),
        ema_trend, warmup=lambda p: p["period"],
        overlays=lambda f, period, **_: [Overlay(f"EMA {period}", ema(f["close"], period), TREND)],
    ),
    Strategy(
        "rsi_reversal", "RSI Reversal",
        "RSI aşırı satımdan yukarı kesince al, çıkış seviyesine ulaşınca sat.",
        (P("period", "RSI periyodu", 14, 2, 100), P("oversold", "Aşırı satım", 30, 5, 50),
         P("exit_level", "Çıkış seviyesi", 55, 40, 95)),
        rsi_reversal, warmup=lambda p: p["period"] + 1, category="mean_reversion",
    ),
    Strategy(
        "macd_trend", "MACD Trend",
        "MACD sinyal çizgisinin üstündeyken long; isteğe bağlı EMA trend filtresi (0 = kapalı).",
        (P("fast_period", "Hızlı", 12, 2, 100), P("slow_period", "Yavaş", 26, 3, 200),
         P("signal_period", "Sinyal", 9, 2, 50), P("trend_period", "Trend EMA (0=kapalı)", 200, 0, 500)),
        macd_trend, warmup=lambda p: max(p["slow_period"] + p["signal_period"], p["trend_period"]),
        overlays=lambda f, trend_period, **_: (
            [Overlay(f"EMA {trend_period}", ema(f["close"], trend_period), TREND)] if trend_period else []
        ),
        check=_ema_check,
    ),
    Strategy(
        "bollinger_breakout", "Bollinger Band Breakout",
        "Kapanış üst bandı kırınca long, orta bandın altına inince çık.",
        (P("period", "Periyot", 20, 5, 200),
         P("std_mult", "Standart sapma", 2.0, 0.5, 4, 0.1, integer=False)),
        bollinger_breakout, warmup=lambda p: p["period"], overlays=_bollinger_lines,
        category="breakout",
    ),
    Strategy(
        "turtle", "Turtle Trading (Donchian)",
        "Önceki N barın en yükseğini kırınca long, önceki M barın en düşüğünün altında çık.",
        (P("entry_period", "Giriş kanalı", 20, 5, 200), P("exit_period", "Çıkış kanalı", 10, 2, 200)),
        turtle, warmup=lambda p: max(p["entry_period"], p["exit_period"]) + 1,
        overlays=lambda f, entry_period, exit_period, **_: [
            Overlay(f"{entry_period} bar en yüksek", f["high"].rolling(entry_period).max().shift(1), FAST),
            Overlay(f"{exit_period} bar en düşük", f["low"].rolling(exit_period).min().shift(1), BAND),
        ],
        category="breakout",
    ),
    Strategy(
        "asia_breakout", "Asia Session Breakout",
        "00–03 UTC (Tokyo açılışı) aralığının üstünde kapanışta long, 08 UTC'de (Londra) kapat.",
        _session_params(0, 3, 8), session_breakout, warmup=lambda p: 24, check=_session_check,
        category="session",
    ),
    Strategy(
        "london_breakout", "London Session Breakout",
        "Asya seansı (00–07 UTC) aralığının üstünde kapanışta long, 16 UTC'de kapat.",
        _session_params(0, 7, 16), session_breakout, warmup=lambda p: 24, check=_session_check,
        category="session",
    ),
]}

TIMEFRAME_AWARE = {"asia_breakout", "london_breakout"}


def build_signal(strategy_id: str, frame: pd.DataFrame, params: dict, timeframe: str) -> pd.Series:
    spec = STRATEGIES[strategy_id]
    kwargs = dict(params)
    if strategy_id in TIMEFRAME_AWARE:
        kwargs["timeframe"] = timeframe
    signal = spec.signal(frame, **kwargs)
    return signal.reset_index(drop=True)
