import type {
  AIStrategy, BacktestResult, CompareResult, CSVInfo, DeskReport, DeskSettings, DeskState, DeskStrategy,
  DiscoveryResult, MarketParams, RulesText, StrategyJSON, StrategySpec,
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
  call<{ enabled: boolean; access_code_required: boolean; provider?: string | null;
         provider_label?: string | null }>("/ai/status");

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

export const discover = (market: MarketParams, n_candidates: number, seed: number,
                         families?: string[]) =>
  call<DiscoveryResult>("/discover", {
    method: "POST", body: JSON.stringify({ ...market, n_candidates, seed, families }),
  });

// ---------------------------------------------------------------- Masa (desk)

export const deskState = () => call<DeskState>("/desk/state");
export const deskReport = (days = 7) => call<DeskReport>(`/desk/report?days=${days}`);

export const deskRun = (access_code?: string) =>
  call<{ new_decisions: number; approved: number; errors: unknown[] }>("/desk/run", {
    method: "POST", body: JSON.stringify({ access_code }),
  });

export const deskAdd = (body: Record<string, unknown>, access_code?: string) =>
  call<DeskStrategy>("/desk/strategies", {
    method: "POST", body: JSON.stringify({ ...body, access_code }),
  });

export const deskSetActive = (id: string, active: boolean, access_code?: string) =>
  call<DeskStrategy>(`/desk/strategies/${id}`, {
    method: "PATCH", body: JSON.stringify({ active, access_code }),
  });

export const deskSaveSettings = (settings: DeskSettings, access_code?: string) =>
  call<DeskSettings>("/desk/settings", {
    method: "PUT", body: JSON.stringify({ ...settings, access_code }),
  });

export const inspectCSV = (csv_data: string) =>
  call<CSVInfo>("/csv/inspect", { method: "POST", body: JSON.stringify({ csv_data }) });
