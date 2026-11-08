export type DataSource = "demo" | "binance" | "yahoo" | "csv";

export interface RiskSettings {
  risk_per_trade_pct: number;
  daily_loss_limit_pct: number | null;
  max_total_loss_pct: number | null;
  stop_type: "atr" | "pct";
  stop_atr_period: number;
  stop_atr_mult: number;
  stop_pct: number;
  take_profit_rr: number | null;
}

export interface RiskInfo {
  worst_day_pct: number;
  worst_day: string | null;
  daily_limit_breaches: number;
  account_failed: boolean;
  failed_at: string | null;
  lowest_equity_pct_of_initial: number;
  avg_r_multiple: number | null;
  avg_position_pct: number;
  avg_monthly_return_pct: number;
  positive_months_pct: number;
  exit_reasons: Record<string, number>;
  trading_days: number;
  config: RiskSettings;
}

export interface MarketParams {
  data_source: DataSource;
  symbol: string;
  timeframe: string;
  bars: number;
  initial_capital: number;
  fee_bps: number;
  slippage_bps: number;
  risk?: RiskSettings | null;
  csv_data?: string | null;
}

export interface CSVInfo {
  bars: number;
  timeframe: string;
  session_market: boolean;
  start: string;
  end: string;
  last_close: number;
  quality: DataQuality;
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
  side?: "long" | "short";
  exit_reason?: string;
  r_multiple?: number | null;
  position_pct?: number;
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
  rules?: RulesText;
  risk?: RiskInfo;
  spec?: StrategyJSON;
}

export type StrategyJSON = Record<string, unknown>;

export interface RulesText {
  indicators: string[];
  entry: string[];
  exit: string[];
  short_entry?: string[];
  short_exit?: string[];
  risk: string[];
}

export interface AIStrategy {
  spec: StrategyJSON;
  explanation: string;
  unsupported: string[];
  rules: RulesText;
  model: string;
}

export interface SavedStrategy {
  id: string;
  name: string;
  text: string;
  spec: StrategyJSON;
  savedAt: string;
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
             metrics?: CompareMetrics; error?: string;
             risk?: Pick<RiskInfo, "worst_day_pct" | "daily_limit_breaches" | "account_failed" |
                                   "avg_monthly_return_pct" | "positive_months_pct"> }[];
}

export interface RunRecord {
  id: number;
  label: string;
  result: BacktestResult;
}

export interface SegmentResult {
  monthly_pct: number;
  net_return_pct: number;
  trades: number;
  win_rate_pct: number;
  profit_factor: number | null;
  max_drawdown_pct: number;
  worst_day_pct: number;
  daily_limit_breaches: number;
  account_failed: boolean;
  avg_r: number | null;
}

export interface DiscoveryCandidate {
  id: string;
  family: string;
  name: string;
  spec: StrategyJSON;
  rules: RulesText;
  risk: { stop_type: "atr"; stop_atr_mult: number; take_profit_rr: number | null };
  train: SegmentResult;
  validation: SegmentResult;
  holdout: SegmentResult;
  deflated_sharpe_prob: number;
  annual_sharpe_selection: number;
  verdict: "robust" | "promising" | "failed_holdout";
  verdict_label: string;
}

export interface DiscoveryResult {
  symbol: string;
  timeframe: string;
  data_source: string;
  synthetic_data: boolean;
  funnel: { generated: number; evaluated: number; passed_train: number; validated: number;
            finalists: number; robust: number };
  thresholds: { min_trades_train: number; min_trades_validation: number; effective_trials: number;
                deflated_sharpe_min: number };
  segments: Record<"train" | "validation" | "holdout",
    { start: string; end: string; bars: number; buy_and_hold_monthly_pct: number }>;
  results: DiscoveryCandidate[];
  elapsed_s: number;
  seed: number;
}

// ---------------------------------------------------------------- Masa (desk)

export type DeskVerdict = "AL" | "BEKLE" | "GEÇ";

export interface DeskSettings {
  initial_capital: number;
  risk_per_trade_pct: number;
  daily_loss_limit_pct: number;
  max_total_loss_pct: number;
  max_open_positions: number;
  fee_bps: number;
  slippage_bps: number;
  data_source: "binance" | "demo";
  watchlist: string[];
  scan_timeframe: string;
  volume_spike_x: number;
  volume_lookback_days: number;
  max_late_bars: number;
}

export interface RStats {
  trades: number;
  wins: number;
  win_rate_pct: number | null;
  profit_factor: number | null;
  avg_r: number | null;
  sum_r: number;
}

export interface DeskStrategy {
  id: string;
  name: string;
  symbol: string;
  timeframe: string;
  verdict: "robust" | "promising";
  active: boolean;
  activated_at: string;
  stop_atr_mult: number;
  take_profit_rr: number | null;
  discovery: { holdout?: SegmentResult; deflated_sharpe_prob?: number };
  rules: RulesText;
  forward: RStats;
  desk_stats: RStats;
}

export interface DeskRole {
  role: "Tarayıcı" | "Giriş-Çıkış" | "Risk" | "Şüpheci" | "Günlükçü";
  text: string;
  status?: "OK" | "RED";
  verdict?: DeskVerdict;
}

export interface DeskDecision {
  id: string;
  strategy_id: string;
  strategy_name: string;
  symbol: string;
  timeframe: string;
  signal_time: string;
  decided_at: string;
  verdict: DeskVerdict;
  risk_status: "OK" | "RED" | null;
  late_bars: number;
  roles: DeskRole[];
  trigger: string;
}

export interface DeskPosition {
  strategy_id: string;
  name: string;
  symbol: string;
  timeframe: string;
  status: "open" | "pending";
  entry_time?: string;
  signal_time?: string;
  entry_price?: number;
  stop_price?: number;
  target_price?: number | null;
  last_price: number;
  r_multiple?: number | null;
  pnl?: number;
  risk_amount?: number;
}

export interface DeskSnapshot {
  at: string;
  trigger: string;
  account: {
    initial: number; realized_equity: number; open_pnl: number; equity: number;
    day_start_equity: number; open_risk: number; day_pnl_pct: number; total_pnl_pct: number;
    day_floor: number; total_floor: number; halted: "daily" | "total" | null;
  };
  positions: DeskPosition[];
  curve: { time: string; equity: number }[];
  errors: { name?: string; symbol?: string; error: string }[];
}

export interface DeskTrade {
  strategy_id: string;
  entry_time: string;
  exit_time: string;
  entry_price: number;
  exit_price: number;
  exit_reason: string;
  r_multiple: number | null;
  return_pct: number;
}

export interface DeskStatus {
  storage: "memory" | "supabase";
  persistent: boolean;
  access_code_required: boolean;
  access_code_set: boolean;
  schedule_secret_set: boolean;
}

export interface DeskState {
  status: DeskStatus;
  settings: DeskSettings;
  strategies: DeskStrategy[];
  snapshot: DeskSnapshot | null;
  decisions: DeskDecision[];
  trades: DeskTrade[];
  volume_spikes: { symbol: string; timeframe: string; bar_time: string; text: string }[];
  runs: { created_at: string; trigger: string; new_decisions: number; approved: number;
          errors: unknown[]; equity: number }[];
}

export interface DeskReport {
  period: { start: string; end: string; days: number };
  runs: number;
  expected_runs: number;
  decisions: { total: number; AL: number; BEKLE: number; "GEÇ": number; risk_red: number; missed: number };
  desk: RStats & { pnl: number; equity: number };
  skeptic_audit: RStats;
  suggestions: string[];
}
