"""Parser for MetaTrader 5 history exports.

MT5 "Symbols > Bars > Export" (and `copy_rates_range` dumped to CSV) produce
tab-separated files like:

    <DATE>	<TIME>	<OPEN>	<HIGH>	<LOW>	<CLOSE>	<TICKVOL>	<VOL>	<SPREAD>
    2024.01.02	00:00:00	1.10420	1.10480	1.10390	1.10450	1234	0	7

Timestamps are in BROKER SERVER TIME (often UTC+2 winter / UTC+3 summer), not
UTC. Pass the broker's offset; DST-switching brokers should export with a fixed
offset or convert upstream. Spread is in points; pass `point` (e.g. 0.00001 for
5-digit EURUSD) to get a spread column in price units for cost modelling.
"""

from __future__ import annotations

import io

import pandas as pd

from app.data_adapters.normalize import normalize_ohlcv


def parse_mt5_csv(text: str, server_utc_offset_hours: float = 0.0,
                  point: float | None = None) -> pd.DataFrame:
    sep = "\t" if "\t" in text.splitlines()[0] else None
    raw = pd.read_csv(io.StringIO(text), sep=sep, engine="python")
    raw.columns = [c.strip().strip("<>").lower() for c in raw.columns]
    if "date" not in raw.columns:
        raise ValueError("MT5 CSV must have a <DATE> column")
    stamp = raw["date"].astype(str)
    if "time" in raw.columns:
        stamp = stamp + " " + raw["time"].astype(str)
    local = pd.to_datetime(stamp, format="mixed")
    raw["timestamp"] = (local - pd.Timedelta(hours=server_utc_offset_hours)).dt.tz_localize("UTC")
    if "tickvol" in raw.columns and ("vol" not in raw.columns or (raw["vol"] == 0).all()):
        raw["volume"] = raw["tickvol"]
    elif "vol" in raw.columns:
        raw["volume"] = raw["vol"]
    else:
        raw["volume"] = 0.0
    frame = normalize_ohlcv(raw)
    if "spread" in raw.columns and point:
        spread = raw.set_index("timestamp")["spread"].astype(float) * point
        frame["spread"] = frame["timestamp"].map(spread).to_numpy()
    return frame
