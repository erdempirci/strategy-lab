import type {
  AIStrategy, BacktestResult, CompareResult, MarketParams, RulesText, StrategyJSON, StrategySpec,
} from "./types";

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

export const aiStatus = () =>
  call<{ enabled: boolean; access_code_required: boolean }>("/ai/status");

export const aiTranslate = (text: string, access_code?: string) =>
  call<AIStrategy>("/ai/strategy", { method: "POST", body: JSON.stringify({ text, access_code }) });

export const validateSpec = (spec: StrategyJSON) =>
  call<{ spec: StrategyJSON; rules: RulesText; warmup_bars: number }>("/custom/validate", {
    method: "POST", body: JSON.stringify({ spec }),
  });

export const runCustom = (market: MarketParams, spec: StrategyJSON) =>
  call<BacktestResult>("/custom/backtest", {
    method: "POST", body: JSON.stringify({ ...market, spec, include_candles: true }),
  });
