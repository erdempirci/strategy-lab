"""Trade-based, long/flat backtest engine.

Execution model (deliberately conservative and explicit):
- A signal is evaluated on the CLOSE of bar i, using only data up to and including bar i.
- The resulting order fills at the OPEN of bar i+1 (no same-bar fills, no look-ahead).
- Slippage moves the fill price against us: buys at open*(1+s), sells at open*(1-s).
- Fees are charged as a percentage of traded notional on both entry and exit.
- Position size: 100% of current equity, compounded, no leverage, no shorting.
- Equity is marked to market on every bar close.
- A position still open on the last bar is closed at the last close (with costs)
  and flagged `forced_exit=True`, so every reported trade is fully accounted for.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from app.services.timeframes import periods_per_year

REQUIRED_COLUMNS = {"timestamp", "open", "high", "low", "close"}


def ema_crossover_signal(frame: pd.DataFrame, fast_period: int, slow_period: int) -> pd.Series:
    """1.0 when fast EMA > slow EMA at bar close, else 0.0. NaN during warm-up."""
    from app.strategies.library import ema_crossover

    if fast_period >= slow_period:
        raise ValueError("fast_period must be less than slow_period")
    return ema_crossover(frame, fast_period, slow_period)


def _validate_frame(frame: pd.DataFrame) -> pd.DataFrame:
    missing = REQUIRED_COLUMNS - set(frame.columns)
    if missing:
        raise ValueError(f"Missing columns: {sorted(missing)}")
    data = frame.sort_values("timestamp").reset_index(drop=True)
    prices = data[["open", "high", "low", "close"]]
    if prices.isna().any().any() or (prices <= 0).any().any():
        raise ValueError("OHLC prices must be positive and non-null")
    if data["timestamp"].duplicated().any():
        raise ValueError("Duplicate timestamps in price data")
    return data


def _max_drawdown(equity: pd.Series) -> float:
    return float((equity / equity.cummax() - 1.0).min())


def _max_consecutive(flags: list[bool]) -> int:
    best = run = 0
    for flag in flags:
        run = run + 1 if flag else 0
        best = max(best, run)
    return best


def _round(value: float | None, digits: int = 4) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return round(float(value), digits)


def run_backtest(
    frame: pd.DataFrame,
    signal: pd.Series,
    timeframe: str = "1h",
    initial_capital: float = 10_000,
    fee_bps: float = 5,
    slippage_bps: float = 2,
    trading_minutes_per_year: int = 365 * 24 * 60,
) -> dict:
    """Run a long/flat backtest from a 0/1 target-position signal aligned to `frame`."""
    if initial_capital <= 0:
        raise ValueError("initial_capital must be positive")
    data = _validate_frame(frame)
    if len(signal) != len(data):
        raise ValueError("signal must have the same length as frame")
    signal = signal.reset_index(drop=True)
    if signal.dropna().empty:
        raise ValueError("Not enough bars for the indicator warm-up")

    fee = fee_bps / 10_000
    slip = slippage_bps / 10_000
    opens = data["open"].to_numpy(float)
    closes = data["close"].to_numpy(float)
    times = data["timestamp"]
    sig = signal.to_numpy(float)
    n = len(data)

    cash = float(initial_capital)
    qty = 0.0
    entry: dict | None = None
    trades: list[dict] = []
    equity = np.empty(n)
    total_fees = 0.0
    bars_in_market = 0

    def close_position(i: int, raw_price: float, forced: bool) -> None:
        nonlocal cash, qty, entry, total_fees
        px = raw_price * (1 - slip)
        proceeds = qty * px
        exit_fee = proceeds * fee
        total_fees += exit_fee
        cash = proceeds - exit_fee
        pnl = cash - entry["capital"]
        trades.append({
            "entry_time": entry["time"],
            "entry_price": entry["price"],
            "exit_time": times.iloc[i].isoformat(),
            "exit_price": round(px, 8),
            "quantity": round(qty, 8),
            "bars_held": i - entry["index"],
            "fees": round(entry["fee"] + exit_fee, 4),
            "pnl": round(pnl, 4),
            "return_pct": round((cash / entry["capital"] - 1) * 100, 4),
            "forced_exit": forced,
        })
        qty = 0.0
        entry = None

    for i in range(n):
        # 1) Execute the order decided at the previous bar's close, at this bar's open.
        if i > 0 and not np.isnan(sig[i - 1]):
            want_long = sig[i - 1] > 0.5
            if want_long and qty == 0.0:
                px = opens[i] * (1 + slip)
                capital = cash
                qty = capital / (px * (1 + fee))
                entry_fee = qty * px * fee
                total_fees += entry_fee
                cash = 0.0
                entry = {"index": i, "time": times.iloc[i].isoformat(), "price": round(px, 8),
                         "capital": capital, "fee": entry_fee}
            elif not want_long and qty > 0.0:
                close_position(i, opens[i], forced=False)
        # 2) Mark to market at the close.
        if qty > 0.0:
            bars_in_market += 1
        equity[i] = cash + qty * closes[i]

    if qty > 0.0:
        close_position(n - 1, closes[-1], forced=True)
        equity[-1] = cash

    return summarize(times, equity, trades, total_fees, bars_in_market, initial_capital,
                     timeframe, trading_minutes_per_year)


def summarize(times: pd.Series, equity: np.ndarray, trades: list[dict], total_fees: float,
              bars_in_market: int, initial_capital: float, timeframe: str,
              trading_minutes_per_year: int = 365 * 24 * 60) -> dict:
    """Metrics, monthly returns and a downsampled equity/drawdown curve."""
    n = len(equity)
    equity_s = pd.Series(equity, index=pd.DatetimeIndex(times))
    bar_returns = equity_s.pct_change().fillna(0.0)
    pnls = np.array([t["pnl"] for t in trades], dtype=float)
    wins, losses = pnls[pnls > 0], pnls[pnls < 0]
    gross_profit, gross_loss = float(wins.sum()), float(-losses.sum())
    final_equity = float(equity_s.iloc[-1])
    net_profit = final_equity - initial_capital
    max_dd = _max_drawdown(equity_s)
    max_dd_abs = float((equity_s.cummax() - equity_s).max())
    std = float(bar_returns.std(ddof=1))
    ann = periods_per_year(timeframe, trading_minutes_per_year)
    sharpe = math.sqrt(ann) * float(bar_returns.mean()) / std if std > 0 else 0.0
    avg_win = float(wins.mean()) if len(wins) else None
    avg_loss = float(losses.mean()) if len(losses) else None

    monthly = equity_s.resample("ME").last()
    monthly_returns = monthly.pct_change()
    monthly_returns.iloc[0] = monthly.iloc[0] / initial_capital - 1

    step = max(1, n // 200)
    curve_idx = list(range(0, n, step))
    if curve_idx[-1] != n - 1:
        curve_idx.append(n - 1)
    drawdown = equity_s / equity_s.cummax() - 1

    return {
        "metrics": {
            "initial_capital": round(float(initial_capital), 2),
            "final_equity": round(final_equity, 2),
            "net_profit": round(net_profit, 2),
            "net_return_pct": round((final_equity / initial_capital - 1) * 100, 4),
            "max_drawdown_pct": round(max_dd * 100, 4),
            "recovery_factor": _round(net_profit / max_dd_abs) if max_dd_abs > 0 else None,
            "trades": len(trades),
            "win_rate_pct": round(len(wins) / len(trades) * 100, 2) if trades else 0.0,
            "profit_factor": _round(gross_profit / gross_loss) if gross_loss > 0 else None,
            "expectancy_per_trade": round(float(pnls.mean()), 4) if trades else 0.0,
            "avg_trade_return_pct": round(float(np.mean([t["return_pct"] for t in trades])), 4)
            if trades else 0.0,
            "avg_win": _round(avg_win),
            "avg_loss": _round(avg_loss),
            "avg_win_loss_ratio": _round(avg_win / abs(avg_loss)) if avg_win and avg_loss else None,
            "max_consecutive_losses": _max_consecutive([p < 0 for p in pnls]),
            "total_fees": round(total_fees, 4),
            "exposure_pct": round(bars_in_market / n * 100, 2),
            "sharpe_annualized": round(sharpe, 4),
            "annualization_periods": round(ann, 2),
        },
        "trades": trades,
        "monthly_returns_pct": [
            {"month": ts.strftime("%Y-%m"), "return_pct": round(float(r) * 100, 4)}
            for ts, r in monthly_returns.items()
        ],
        "equity_curve": [
            {"timestamp": times.iloc[i].isoformat(), "equity": round(float(equity[i]), 2),
             "drawdown_pct": round(float(drawdown.iloc[i]) * 100, 4)}
            for i in curve_idx
        ],
    }


def run_ema_backtest(
    frame: pd.DataFrame,
    fast_period: int = 12,
    slow_period: int = 26,
    initial_capital: float = 10_000,
    fee_bps: float = 5,
    slippage_bps: float = 2,
    timeframe: str = "1h",
) -> dict:
    if len(frame) < slow_period + 2:
        raise ValueError(f"Need at least slow_period + 2 = {slow_period + 2} bars")
    data = _validate_frame(frame)
    signal = ema_crossover_signal(data, fast_period, slow_period)
    return run_backtest(data, signal, timeframe, initial_capital, fee_bps, slippage_bps)
