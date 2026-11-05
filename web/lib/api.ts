import type { BacktestResult, CompareResult, MarketParams, StrategySpec } from "./types";

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(`/api${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
  });
  const body = await resp.json().catch(() => null);
  if (!resp.ok) {
    const detail = body?.detail;
    const message = Array.isArray(detail)
      ? detail.map((d: { msg?: string }) => d.msg?.replace(/^Value error, /, "")).join("; ")
      : detail || `API hatası ${resp.status}`;
    throw new Error(message);
  }
  return body as T;
}

export const fetchStrategies = () =>
  call<{ strategies: StrategySpec[] }>("/strategies").then((r) => r.strategies);

export const runBacktest = (market: MarketParams, strategy: string, params: Record<string, number>) =>
  call<BacktestResult>("/backtests", {
    method: "POST",
    body: JSON.stringify({ ...market, strategy, params, include_candles: true }),
  });

export const compareAll = (market: MarketParams) =>
  call<CompareResult>("/compare", { method: "POST", body: JSON.stringify(market) });
