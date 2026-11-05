# Strategy Lab — Dashboard

Next.js app: strategy parameters, metrics, candlestick chart with EMA lines and trade
markers, equity + drawdown, monthly returns, trade list, in-session run comparison.

Calls same-origin `/api/*`. On Vercel the project's `vercel.json` routes that to the API
service. Locally, `API_URL=http://127.0.0.1:8000 npm run dev` proxies it to uvicorn.
