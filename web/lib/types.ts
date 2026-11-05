export type DataSource = "demo" | "binance";

export interface MarketParams {
  data_source: DataSource;
  symbol: string;
  timeframe: string;
  bars: number;
  initial_capital: number;
  fee_bps: number;
  slippage_bps: number;
}

export interface ParamSpec {
  name: string;
  label: string;
  default: number;
  min: number;
  max: number;
  step: number;
  integer: boolean;
}

export interface StrategySpec {
  id: string;
  name: string;
  description: string;
  category: string;
  params: ParamSpec[];
}

export interface Trade {
  entry_time: string;
  entry_price: number;
  exit_time: string;
  exit_price: number;
  quantity: number;
  bars_held: number;
  fees: number;
  pnl: number;
  return_pct: number;
  forced_exit: boolean;
}

export interface Candle {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
}

export interface OverlayLine {
  name: string;
  color: string | null;
  data: { time: number; value: number }[];
}

export interface Metrics {
  initial_capital: number;
  final_equity: number;
  net_profit: number;
  net_return_pct: number;
  max_drawdown_pct: number;
  recovery_factor: number | null;
  trades: number;
  win_rate_pct: number;
  profit_factor: number | null;
  expectancy_per_trade: number;
  avg_trade_return_pct: number;
  avg_win: number | null;
  avg_loss: number | null;
  avg_win_loss_ratio: number | null;
  max_consecutive_losses: number;
  total_fees: number;
  exposure_pct: number;
  sharpe_annualized: number;
}

export interface DataQuality {
  bars: number;
  start: string | null;
  end: string | null;
  gaps: number;
  missing_bars_estimate: number;
  inconsistent_high_low_bars: number;
}

export interface BacktestResult {
  symbol: string;
  timeframe: string;
  strategy: string;
  strategy_name: string;
  parameters: Record<string, number>;
  data_source: string;
  synthetic_data: boolean;
  data_quality: DataQuality;
  engine_version: string;
  metrics: Metrics;
  trades: Trade[];
  monthly_returns_pct: { month: string; return_pct: number }[];
  equity_curve: { timestamp: string; equity: number; drawdown_pct: number }[];
  candles: Candle[];
  overlays: OverlayLine[];
}

export type CompareMetrics = Pick<
  Metrics,
  "net_return_pct" | "max_drawdown_pct" | "trades" | "win_rate_pct" | "profit_factor" |
  "sharpe_annualized" | "exposure_pct" | "recovery_factor" | "total_fees"
>;

export interface CompareResult {
  symbol: string;
  timeframe: string;
  data_source: string;
  synthetic_data: boolean;
  data_quality: DataQuality;
  note: string;
  results: { strategy: string; name: string; parameters?: Record<string, number>;
             metrics?: CompareMetrics; error?: string }[];
}

export interface RunRecord {
  id: number;
  label: string;
  result: BacktestResult;
}
