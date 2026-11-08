"""User-uploaded OHLCV CSV — mainly TradingView "Export chart data" files.

TradingView exports look like:
    time,open,high,low,close,Volume,<indicator columns...>
    1704067200,42283.58,42554.57,42261.02,42475.23,1271.68,...
with `time` either a UNIX timestamp (seconds) or ISO 8601 depending on the export option.
Generic files with Date/Time/Open/High/Low/Close[/Volume] headers also work. Extra
columns are ignored. Timestamps are converted to UTC; naive ISO times are taken as UTC.

The bar interval is inferred from the data (median spacing) so the backtest uses the
right calendar; the uploaded data itself is never altered beyond sorting/deduplication.
"""

from __future__ import annotations

import io

import numpy as np
import pandas as pd

from app.data_adapters.normalize import normalize_ohlcv

MAX_ROWS = 100_000
TIME_NAMES = ("time", "timestamp", "date", "datetime", "<date>")
ALIASES = {"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume", "vol": "volume",
           "tickvol": "volume", "<open>": "open", "<high>": "high", "<low>": "low",
           "<close>": "close", "<tickvol>": "volume", "<vol>": "volume"}


class CSVDataError(ValueError):
    pass


def _parse_time(col: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(col, errors="coerce")
    if numeric.notna().all():
        unit = "ms" if numeric.median() > 1e11 else "s"
        return pd.to_datetime(numeric.astype("int64"), unit=unit, utc=True)
    ts = pd.to_datetime(col.astype(str).str.strip(), utc=True, errors="coerce", format="mixed")
    if ts.isna().any():
        bad = col[ts.isna()].iloc[0]
        raise CSVDataError(f"Zaman sütunu okunamadı (örnek: {bad!r})")
    return ts


def _label(minutes: int) -> str:
    if minutes % 1440 == 0:
        return f"{minutes // 1440}d"
    if minutes % 60 == 0:
        return f"{minutes // 60}h"
    return f"{minutes}m"


def infer_timeframe(timestamps: pd.Series) -> str:
    diffs = timestamps.diff().dropna().dt.total_seconds() / 60
    diffs = diffs[diffs > 0]
    if diffs.empty:
        raise CSVDataError("Zaman aralığı belirlenemedi (en az 2 bar gerekli)")
    return _label(max(1, round(float(np.median(diffs)))))


def parse_ohlcv_csv(text: str) -> tuple[pd.DataFrame, str]:
    """Return (normalized frame, inferred timeframe label like '1h')."""
    if not text or not text.strip():
        raise CSVDataError("CSV boş")
    first = text.lstrip().splitlines()[0]
    sep = ";" if first.count(";") > first.count(",") else ("\t" if "\t" in first else ",")
    try:
        raw = pd.read_csv(io.StringIO(text), sep=sep, nrows=MAX_ROWS + 1)
    except Exception as exc:  # pandas raises several parser error types
        raise CSVDataError(f"CSV okunamadı: {exc}") from exc
    if len(raw) > MAX_ROWS:
        raise CSVDataError(f"En fazla {MAX_ROWS} satır yüklenebilir")
    raw.columns = [str(c).strip().lower() for c in raw.columns]
    raw = raw.rename(columns={c: ALIASES.get(c, c) for c in raw.columns})
    time_col = next((c for c in TIME_NAMES if c in raw.columns), None)
    if time_col is None:
        raise CSVDataError("Zaman sütunu bulunamadı (time / date / timestamp)")
    if time_col in ("date", "<date>") and ("time" in raw.columns or "<time>" in raw.columns):
        tcol = "time" if "time" in raw.columns else "<time>"
        stamp = raw[time_col].astype(str) + " " + raw[tcol].astype(str)
    else:
        stamp = raw[time_col]
    missing = [c for c in ("open", "high", "low", "close") if c not in raw.columns]
    if missing:
        raise CSVDataError(f"Eksik sütun(lar): {', '.join(missing)}")
    if "volume" not in raw.columns:
        raw["volume"] = 0.0
    raw["timestamp"] = _parse_time(stamp)
    raw = raw.dropna(subset=["open", "high", "low", "close"])
    try:
        frame = normalize_ohlcv(raw)
    except (ValueError, TypeError) as exc:
        raise CSVDataError(f"Fiyat sütunları sayı değil: {exc}") from exc
    if len(frame) < 2:
        raise CSVDataError("CSV'de en az 2 bar olmalı")
    if (frame[["open", "high", "low", "close"]] <= 0).any().any():
        raise CSVDataError("CSV'de sıfır veya negatif fiyat var")
    return frame, infer_timeframe(frame["timestamp"])


def looks_like_session_market(frame: pd.DataFrame) -> bool:
    """True when (almost) no bars fall on Saturdays — FX, metals, indices, stocks."""
    sat = (frame["timestamp"].dt.weekday == 5).mean()
    return bool(sat < 0.01)
