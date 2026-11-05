import type { BacktestParams, BacktestResult } from "./types";

export async function runBacktest(params: BacktestParams): Promise<BacktestResult> {
  const resp = await fetch("/api/backtests", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ...params, strategy: "ema_crossover", include_candles: true }),
  });
  const body = await resp.json().catch(() => null);
  if (!resp.ok) {
    const detail = body?.detail;
    const message = Array.isArray(detail)
      ? detail.map((d: { msg?: string }) => d.msg?.replace(/^Value error, /, "")).join("; ")
      : detail || `API error ${resp.status}`;
    throw new Error(message);
  }
  return body as BacktestResult;
}
