# Architecture decisions

- **One repo, two services, one deployment.** `api/` (FastAPI) and `web/` (Next.js) are
  built separately and served on one domain through Vercel Services (`vercel.json`):
  `/api/*` → API, everything else → dashboard.
- The browser only calls same-origin `/api/*`. No CORS, no API URL in the client.
- Historical bars are normalized to UTC OHLCV (`api/app/data_adapters/normalize.py`).
- Bulk bars will live in Parquet + DuckDB; PostgreSQL (Supabase) holds metadata and results.
- Strategies will be versioned declarative specifications (JSON DSL), never arbitrary
  model-generated code.
- Long backtests / optimisation run in workers (Redis + RQ on a VPS), not in request handlers.
- Every run reports data source, data range, data quality, parameters, costs, engine
  version and execution assumptions.
- MT5 runs only on Windows; bars arrive as CSV (see `api/app/data_adapters/README.md`).
- No live execution.
