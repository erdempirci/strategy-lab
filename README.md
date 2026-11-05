# Strategy Lab

AI-assisted, multi-market trading strategy research and backtesting platform.

> Research only. No live order execution. Backtest results are hypothetical and do not
> imply future performance.

```
strategy-lab/
├── api/          FastAPI · backtest engine · data adapters (Binance, MT5 CSV)
├── web/          Next.js dashboard · TradingView Lightweight Charts
├── docs/         architecture decisions
└── vercel.json   one Vercel project: /api/* → api, /* → web
```

## Deploy (Vercel, one project)
Import the repo → Root Directory `./` → Application Preset **Services** → Deploy.
No environment variables required (optional: `BINANCE_BASE_URL`).

- `/` dashboard
- `/api/health` · `/api/docs` (Swagger) · `POST /api/backtests` · `GET /api/market-data/binance`

## Run locally
```bash
# API
cd api && python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]" && uvicorn app.main:app --reload --port 8000

# Dashboard (second terminal)
cd web && npm install && API_URL=http://127.0.0.1:8000 npm run dev
```
Open http://localhost:3000. Or run both with `cp .env.example .env && docker compose up`.

Tests: `cd api && pytest` · `cd web && npm run build`

## Backtest engine (v0.2.0)
Trade-based, long/flat (`api/app/services/backtester.py`):
- Signal at bar close → fill at **next bar open** (no look-ahead).
- Slippage applied against the fill price; fees as % of notional on entry and exit.
- 100% of equity per trade, compounded, no leverage; equity marked to market every bar.
- A position open on the last bar is closed at the last close and flagged `forced_exit`.
- Win rate, profit factor, expectancy, consecutive losses are **per round-trip trade**.
- Sharpe annualized from the timeframe on a 24/7 calendar (crypto).
- Every response includes `data_source`, `synthetic_data` and a `data_quality` report.

Not yet modelled: intrabar stop/target, shorting, funding, swaps, contract multipliers,
market calendars.

## Data
- **Binance** spot, public klines, closed candles only. Default host
  `data-api.binance.vision` (`api.binance.com` returns 451 for US IPs, incl. Vercel).
- **MT5** CSV exports → UTC with the broker's server offset, historical spread kept.
- **Demo** deterministic synthetic data — clearly flagged, never real performance.

## Roadmap
1. ✅ Reliable trade-based backtest engine, real Binance data, MT5 CSV parser
2. ✅ Dashboard
3. Strategy library (EMA200 trend, RSI, MACD, Bollinger, session breakouts, Fibonacci, Turtle, FVG)
4. AI strategy builder → validated JSON DSL
5. Optimisation (grid search, multi-symbol / multi-timeframe)
6. Validation: out-of-sample, walk-forward, Monte Carlo, overfitting checks
7. Funded / prop-firm risk simulator
8. Paper trading
