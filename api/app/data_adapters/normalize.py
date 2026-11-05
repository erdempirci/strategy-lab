"""OHLCV normalization and data-quality checks shared by every adapter."""

from __future__ import annotations

import pandas as pd

from app.services.timeframes import timeframe_minutes

COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]


def normalize_ohlcv(frame: pd.DataFrame) -> pd.DataFrame:
    """Coerce to UTC timestamps + float OHLCV, sort, drop exact duplicate bars."""
    missing = set(COLUMNS) - set(frame.columns)
    if missing:
        raise ValueError(f"Missing columns: {sorted(missing)}")
    data = frame[COLUMNS].copy()
    ts = pd.to_datetime(data["timestamp"], utc=True)
    data["timestamp"] = ts
    for col in COLUMNS[1:]:
        data[col] = pd.to_numeric(data[col], errors="raise").astype(float)
    data = data.sort_values("timestamp").drop_duplicates("timestamp", keep="last")
    return data.reset_index(drop=True)


def quality_report(frame: pd.DataFrame, timeframe: str,
                   normal_gap: pd.Timedelta | None = None) -> dict:
    """Describe problems a backtest should know about. Does not modify data.

    normal_gap: largest gap that is expected (e.g. 4 days for weekends + holidays on stock
    exchanges); longer gaps are reported.
    """
    step = pd.Timedelta(minutes=timeframe_minutes(timeframe))
    deltas = frame["timestamp"].diff().dropna()
    gaps = deltas[deltas > max(step, normal_gap or step)]
    bad_hl = (
        (frame["high"] < frame[["open", "close"]].max(axis=1))
        | (frame["low"] > frame[["open", "close"]].min(axis=1))
        | (frame["high"] < frame["low"])
    )
    non_positive = (frame[["open", "high", "low", "close"]] <= 0).any(axis=1)
    return {
        "bars": len(frame),
        "start": frame["timestamp"].iloc[0].isoformat() if len(frame) else None,
        "end": frame["timestamp"].iloc[-1].isoformat() if len(frame) else None,
        "gaps": len(gaps),
        "missing_bars_estimate": int(sum(int(d / step) - 1 for d in gaps)),
        "largest_gap": str(gaps.max()) if len(gaps) else None,
        "inconsistent_high_low_bars": int(bad_hl.sum()),
        "non_positive_price_bars": int(non_positive.sum()),
        "zero_volume_bars": int((frame["volume"] == 0).sum()),
    }
