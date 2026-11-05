"""Timeframe helpers shared by data adapters and the backtester."""

import re

_UNIT_MINUTES = {"m": 1, "h": 60, "d": 1440, "w": 10080}
_PATTERN = re.compile(r"^(\d+)([mhdw])$")


def timeframe_minutes(timeframe: str) -> int:
    """Return bar length in minutes for strings like '1m', '15m', '4h', '1d', '1w'."""
    match = _PATTERN.match(timeframe.strip().lower())
    if not match:
        raise ValueError(f"Unsupported timeframe: {timeframe!r} (use e.g. 1m, 15m, 1h, 4h, 1d, 1w)")
    value, unit = int(match.group(1)), match.group(2)
    if value <= 0:
        raise ValueError("timeframe value must be positive")
    return value * _UNIT_MINUTES[unit]


def pandas_freq(timeframe: str) -> str:
    return f"{timeframe_minutes(timeframe)}min"


def periods_per_year(timeframe: str, trading_minutes_per_year: int = 365 * 24 * 60) -> float:
    """Bars per year. Default assumes a 24/7 market (crypto).

    For FX (~24/5) pass trading_minutes_per_year = 260 * 24 * 60.
    """
    return trading_minutes_per_year / timeframe_minutes(timeframe)
