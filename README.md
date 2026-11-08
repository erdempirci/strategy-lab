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
Environment variables (all optional):

| Variable | Purpose |
|----------|---------|
| `GEMINI_API_KEY` | AI strategy builder via Gemini — free key from Google AI Studio |
| `NVIDIA_API_KEY` | AI strategy builder via NVIDIA build — free key from build.nvidia.com |
| `ANTHROPIC_API_KEY` | AI strategy builder via Claude (paid API) |
| `AI_PROVIDER` | `gemini` / `nvidia` / `anthropic` when more than one key is set (default order: anthropic, gemini, nvidia) |
| `GEMINI_MODEL` / `NVIDIA_MODEL` / `ANTHROPIC_MODEL` | defaults `gemini-3.8-flash` / `meta/llama-3.1-70b-instruct` / `claude-sonnet-5-5` |
| `GEMINI_REASONING_EFFORT` | Gemini thinking level: `none` / `low` (default, fast) / `medium` / `high` |
| `AI_ACCESS_CODE` | if set, the AI builder asks for this code (protects your quota) |
| `BINANCE_BASE_URL` | default `https://data-api.binance.vision` |
| `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` | Masa journal storage (server side only); a secret key `sb_secret_…` or a legacy `service_role` key |
| `DESK_ACCESS_CODE` | required to change the desk (add strategies, settings, run now) |
| `CRON_SECRET` | lets the hourly GitHub Action run the desk |

- `/` dashboard
- `/api/health` · `/api/docs` (Swagger) · `GET /api/strategies` · `POST /api/backtests` ·
  `POST /api/compare` · `GET /api/market-data/binance`

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

## Strategies (`api/app/strategies/`)
| id | Strategy | Logic (long/flat) |
|----|----------|-------------------|
| `ema_crossover` | EMA Crossover | fast EMA > slow EMA |
| `ema_trend` | EMA200 Trend Following | close above EMA (+ optional confirm %), exit below |
| `rsi_reversal` | RSI Reversal | RSI crosses up through oversold, exit at exit level |
| `macd_trend` | MACD Trend | MACD > signal, optional EMA trend filter |
| `bollinger_breakout` | Bollinger Breakout | close > upper band, exit below middle band |
| `turtle` | Turtle (Donchian) | close > prior N-bar high, exit < prior M-bar low |
| `asia_breakout` | Asia Session Breakout | close above 00–03 UTC range, flat by 08 UTC |
| `london_breakout` | London Session Breakout | close above 00–07 UTC range, flat by 16 UTC |

**Video presets** (`api/app/strategies/presets.py`) — the six strategies from Quantified
Strategies' *"6 RSI Trading Strategies Tested"*, transcribed rule by rule as DSL specs:
`qs_rsi2`, `qs_rsi_drop`, `qs_triple_rsi`, `qs_normalized_rsi`, `qs_rsi_momentum`,
`qs_rsi_pullback`. Their reference results are SPY, daily, no costs; Triple RSI's exit is not
stated in the video (RSI(5) > 50 from the channel's published version is used).

Every strategy is a pure function of bars `0..i` and is covered by a generic
no-look-ahead test. Fibonacci 0.618 pullback and FVG + market structure need intrabar
stop-loss / take-profit and come with the SL/TP engine.

`POST /api/compare` runs all strategies with default parameters on the same data.

## Custom strategies + AI builder (`api/app/strategies/dsl.py`, `api/app/ai/`)
Users describe a strategy in plain Turkish; Claude translates it into a declarative JSON
DSL which is validated before anything runs — **no generated code is ever executed**.

```json
{
  "name": "EMA200 + RSI",
  "indicators": [{"id": "ema200", "type": "ema", "period": 200},
                 {"id": "rsi14", "type": "rsi", "period": 14}],
  "entry": {"all": [{"left": "close", "op": ">", "right": "ema200"},
                    {"left": "rsi14", "op": "crosses_above", "right": "30"}]},
  "exit":  {"any": [{"left": "rsi14", "op": ">=", "right": "70"}]},
  "stop_loss_pct": 2, "take_profit_pct": 6, "max_bars_in_trade": 48
}
```
- Indicators: `sma ema rsi roc highest lowest atr normalize macd(.line/.signal/.hist) bb(.upper/.middle/.lower)`
- `"of": "<earlier indicator>"` applies sma/ema/highest/lowest/normalize to another indicator
  (e.g. highest RSI(14) of 100 bars)
- Operands: `open high low close volume hour weekday`, indicator ids, numbers, look-back `close[1]`
- Operators: `> < >= <= crosses_above crosses_below`; `all` = AND, `any` = OR
- Stops/targets are close-based vs. the actual fill price.
- Anything the DSL cannot express is returned as `unsupported` and shown to the user.

Endpoints: `GET /api/ai/status`, `POST /api/ai/strategy`, `POST /api/custom/validate`,
`POST /api/custom/backtest`.

## Risk engine (`api/app/services/risk_engine.py`)
Send `"risk": {...}` with any backtest / compare request (the dashboard does by default):

| Rule | Default | Behaviour |
|------|---------|-----------|
| `risk_per_trade_pct` | 0.5 | size = equity × risk ÷ loss-per-unit at the stop, capped at 1× equity |
| `daily_loss_limit_pct` | 3 | vs. UTC day-start equity incl. open P&L at the bar low → close, no new trades that day |
| `max_total_loss_pct` | 10 | static vs. initial capital → close, account stops trading ("failed") |
| `stop_type` | `atr` | `atr` (`stop_atr_mult` × ATR(`stop_atr_period`)) or `pct` (`stop_pct`) |
| `take_profit_rr` | 2 | target at R multiple; `null` = exit on signal only |

Intrabar: the highest protective level is touched first; gaps fill at the open; if stop and
target are both inside a bar the stop is assumed first. Entries need a fresh 0→1 signal.
Custom strategies' `stop_loss_pct` / `take_profit_pct` override the stop/target.

## Strategy discovery (`api/app/discovery/`)
`POST /api/discover` (dashboard: **Keşfet**) searches for strategies under your risk rules
and reports only what survives data it never saw:

1. **Generate** 20–300 DSL candidates (8 families × trend / session / weekday filters ×
   stop / target settings), seeded and reproducible.
2. **Split chronologically**: train 60% · validation 20% · **holdout 20%**.
3. **Train**: needs enough trades, PF ≥ 1.05, positive month-equivalent return, no rule breach.
4. **Validation**: top 25 must stay profitable with no breach; ranked by min(train, validation).
5. **Holdout**: the top 5 are measured once. Holdout never influences selection (tested).
6. **Deflated Sharpe** (Bailey & López de Prado) with the *effective* number of independent
   trials (participation ratio of the trials' return-correlation eigenvalues).

Verdicts: **robust** (holdout profitable, no breach, DSR ≥ 0.9) · **promising** (holdout
profitable, luck not ruled out) · **failed holdout**.

Calibration (tests): 0 robust picks on random-walk markets across seeds; finds a planted
momentum edge. On real markets "nothing robust found" is a normal, useful outcome.

## US index futures (`api/app/data_adapters/yahoo.py`, `markets.py`)
- `US100` → `NQ=F` (Nasdaq 100 futures), `US500` → `ES=F`, from Yahoo's public chart API
  (no key). Aliases: NAS100, NQ, NDX, USTEC / SPX500, ES, SPX.
- 1h (≈ 2 years max), 2h/4h (resampled from 60m, UTC-aligned) and 1d.
- Quotes are ~10 min delayed: a bar only counts as closed 15 min after its end, so the
  hourly desk run is at :17.
- Session market: weekend and daily-break gaps (< 80 h) are not reported as data gaps;
  the Tarayıcı compares volume with the same UTC hour's average.
- Default costs 0.5 + 0.5 bps (NQ commission + 1 tick ≈ 0.2 bps). The continuous contract
  jumps at quarterly rolls. Long only, like the rest of the engine.
- Keşfet can be limited to one strategy family, e.g. `families: ["engulfing"]`
  (bullish engulfing with trend / low-sweep / RSI-pullback context, bearish engulfing exit).

## Masa — trading desk (`api/app/desk/`)
Forward-tests discovered strategies on a **paper** account, hourly. Rule-based roles, no LLM,
no orders:

| Role | Does |
|------|------|
| Tarayıcı | fresh entry signal of a desk strategy on the last closed bar; volume ≥ 2× its 30-day average on the watchlist |
| Giriş-Çıkış | entry = next bar open, stop = strategy's ATR multiple, target = its R:R |
| Risk | sizes from desk equity (0.5%/trade); **RED** if today −3%, total −10%, open-position cap, or if *all open stops plus this one* would cross a limit |
| Şüpheci | AL / BEKLE / GEÇ: waits on "promising" strategies until 10 live trades prove them, blocks a strategy whose live PF fell < 0.8, waits on extreme volatility, same-symbol exposure or data gaps |
| Günlükçü | journals every decision with each role's message; weekly report incl. an audit of what the Şüpheci's vetoes would have earned |

- Only **robust/promising** Keşfet finalists can join the desk ("Masaya ekle"); timeframes ≥ 1h.
- Paper trades are produced by `run_risk_backtest` itself on a signal that keeps only approved
  entries, so desk results and backtests cannot drift apart (tested). Closed trades are frozen;
  every signal, approved or not, is also tracked in a **shadow** book.
- Only the signal of the last closed bar can be approved; older ones are journaled as missed.
- Because Risk gates on worst case (every open stop hit), the daily / total limits can only be
  crossed by price gaps; positions are not force-closed.
- Endpoints: `GET /api/desk/status|state|report`, `POST /api/desk/run`,
  `POST /api/desk/strategies`, `PATCH /api/desk/strategies/{id}`, `PUT /api/desk/settings`.

Setup: create a Supabase project → run `supabase/migrations/001_desk.sql` in the SQL editor →
set `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `DESK_ACCESS_CODE`, `CRON_SECRET` on Vercel →
add repo secrets `DESK_URL` and `CRON_SECRET` for `.github/workflows/desk.yml` (hourly at :17;
Vercel Hobby crons are daily-only). GitHub pauses scheduled workflows after 60 days without
repo activity; re-enable from the Actions tab.

## Backtest engine
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
- **Stocks / ETFs** daily on the Yahoo source (`data_source: "yahoo"` with any symbol other
  than a named future, e.g. SPY, QQQ): dividend/split-adjusted, Stooq fallback; today's bar
  dropped until the US close. Sharpe uses 252 sessions/year; weekends are not gaps.
- **Gold / index futures** on the Yahoo source: `XAUUSD` (GC=F, aliases GOLD/XAU/ALTIN),
  `US100` (NQ=F), `US500` (ES=F); 1h/2h/4h/1d, 1h history ≈ 2 years.
- **CSV upload** (`data_source: "csv"`, `csv_data`): TradingView "Export chart data" (UNIX or
  ISO time), generic Date/Open/High/Low/Close, MT5 exports. Interval inferred from the data and
  must match `timeframe`; files with no Saturday bars use the 252-session calendar.
  `POST /api/csv/inspect` previews what will be tested. Up to 100,000 rows.
- Up to **50,000 bars** per request; the chart shows the latest 5,000, metrics use all bars.
- **Demo** deterministic synthetic data — clearly flagged, never real performance.

## Roadmap
1. ✅ Reliable trade-based backtest engine, real Binance data, MT5 CSV parser
2. ✅ Dashboard
3. ✅ Strategy library (8 strategies) + side-by-side comparison
4. ✅ Risk engine: intrabar SL/TP, risk-based sizing, daily / total loss limits
   (Fibonacci 0.618 and FVG strategies still to add)
5. ✅ AI strategy builder → validated JSON DSL ("Kendi stratejim")
6. ✅ Discovery: search + train/validation/holdout + deflated Sharpe
7. Next: walk-forward folds, Monte Carlo, multi-symbol robustness
8. ✅ Masa: hourly paper-trading desk with rule-based roles and a journal
9. ✅ US100 / US500 futures data (Yahoo) for Keşfet and the desk
10. Short side in the engine
11. Funded / prop-firm risk simulator
