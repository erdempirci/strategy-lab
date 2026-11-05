# Data adapters

Every adapter returns normalized UTC OHLCV via `normalize.normalize_ohlcv`:
`timestamp, open, high, low, close, volume` (+ optional `spread`).
`normalize.quality_report` flags gaps, missing bars and inconsistent OHLC.

## Binance (`binance.py`) — implemented
- Public `/api/v3/klines`, no API key, paginated (1000 bars/request), retries on 418/429.
- Default host `https://data-api.binance.vision` (market-data only). `api.binance.com`
  returns HTTP 451 for US IPs, which includes Vercel's default `iad1` region.
  Override with `BINANCE_BASE_URL`.
- The still-forming last candle is dropped.
- CCXT can be added later for other exchanges behind the same return contract.

## MetaTrader 5 (`mt5_csv.py`) — CSV parser implemented, ingestion designed
The MT5 terminal and `MetaTrader5` Python package are Windows-only, so MT5 never
runs inside the Mac/Docker/Vercel stack.

Flow:
1. **Windows box (PC/VM/VPS)** runs MT5 + a small exporter script
   (`mt5.copy_rates_range(symbol, timeframe, from, to)`) on a schedule.
2. Exporter writes MT5-format CSV (or the terminal's own Bars export).
3. Files are moved to the Mac (shared folder / rsync / later: authenticated
   `POST /market-data/mt5/import`) and parsed with `parse_mt5_csv`.
4. Parsed bars are stored as Parquet partitioned by `source/symbol/timeframe/year`
   and queried with DuckDB.

Rules:
- MT5 timestamps are **broker server time**. Store the broker's UTC offset (and DST
  policy) per broker profile and convert on import.
- Keep the exported `<SPREAD>` column (points × `point`) so FX backtests can use
  real historical spreads instead of a flat bps cost.
- Record broker name, account type and symbol suffix (e.g. `EURUSD.r`) as metadata:
  prices differ between brokers.
