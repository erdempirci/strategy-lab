"""Risk-managed backtest engine (prop-firm style). Long and short (signal 1 / -1).

Differences from the simple engine (`backtester.run_backtest`):
- Position size from risk: qty = equity × risk% / (loss per unit if the stop fills),
  capped at 1× equity (spot, no leverage).
- Every trade has a stop (ATR multiple or %), optionally a take-profit at R:R.
- Stops / targets / loss limits are checked INTRABAR with the bar's low/high:
    * price moving down from the open touches the HIGHEST protective level first;
    * an open already beyond a level fills at the open (gap);
    * if both stop and target are inside the bar, the stop is assumed first.
- Daily loss limit: equity (incl. open P&L, measured at the bar low) may not fall more
  than X% below the day's starting equity (UTC day). On breach the position is closed at
  the limit level and no new trades are opened until the next day.
- Max total loss: equity may never fall below initial × (1 − Y%). On breach the position
  is closed and the account stops trading ("failed").
- Entries need a FRESH signal (0 → 1). After a stop-out the strategy waits for the next
  entry signal instead of re-entering immediately. Exits on a 1 → 0 signal at next open.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from app.services.backtester import _validate_frame, summarize


@dataclass(frozen=True)
class RiskConfig:
    risk_per_trade_pct: float = 0.5
    daily_loss_limit_pct: float | None = 3.0
    max_total_loss_pct: float | None = 10.0
    stop_type: str = "atr"            # "atr" | "pct"
    stop_atr_period: int = 14
    stop_atr_mult: float = 2.0
    stop_pct: float = 2.0
    take_profit_rr: float | None = 2.0
    max_leverage: float = 1.0


def wilder_atr(frame: pd.DataFrame, period: int) -> pd.Series:
    prev = frame["close"].shift(1)
    tr = pd.concat([frame["high"] - frame["low"], (frame["high"] - prev).abs(),
                    (frame["low"] - prev).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / period, adjust=False).mean()
    atr.iloc[:period] = np.nan
    return atr


def run_risk_backtest(
    frame: pd.DataFrame,
    signal: pd.Series,
    risk: RiskConfig,
    timeframe: str = "1h",
    initial_capital: float = 10_000,
    fee_bps: float = 5,
    slippage_bps: float = 2,
    stop_pct_override: float | None = None,
    take_profit_pct_override: float | None = None,
    return_daily: bool = False,
    trading_minutes_per_year: int = 365 * 24 * 60,
    stop_levels: np.ndarray | None = None,
    take_profit_rr_override: float | None = None,
    events: dict | None = None,
) -> dict:
    """stop_levels: per-bar stop price decided at the signal bar's close (structural stop,
    e.g. the signal candle's low). Overrides the ATR/% stop when given.

    events: rule-based strategies (the DSL) pass their entry/exit EVENTS instead of relying
    on the state signal, so that a position closed by a stop or target can be re-entered on
    the next entry event:
        entry      array of +1 / -1 / 0 at the signal bar (new entry setup on that bar)
        exit_long  bool array, long exit rule true at that bar
        exit_short bool array, short exit rule true at that bar
        max_bars   int | None, close at the open after this many bars in the trade
    """
    if initial_capital <= 0:
        raise ValueError("initial_capital must be positive")
    data = _validate_frame(frame)
    if len(signal) != len(data):
        raise ValueError("signal must have the same length as frame")
    sig = signal.reset_index(drop=True).to_numpy(float)
    if np.isnan(sig).all():
        raise ValueError("Not enough bars for the indicator warm-up")

    fee, slip = fee_bps / 10_000, slippage_bps / 10_000
    r = risk.risk_per_trade_pct / 100
    o, h, lo, c = (data[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    times = data["timestamp"]
    days = pd.to_datetime(times, utc=True).dt.date.to_numpy()
    use_pct = stop_pct_override is not None or risk.stop_type == "pct"
    if events is not None:
        ev_in = np.asarray(events["entry"], float)
        ev_xl = np.asarray(events.get("exit_long", np.zeros(len(data), bool)), bool)
        ev_xs = np.asarray(events.get("exit_short", np.zeros(len(data), bool)), bool)
        ev_max = events.get("max_bars")
        if not (len(ev_in) == len(ev_xl) == len(ev_xs) == len(data)):
            raise ValueError("events must have the same length as frame")
    stop_pct = (stop_pct_override if stop_pct_override is not None else risk.stop_pct) / 100
    atr = None if use_pct else wilder_atr(data, risk.stop_atr_period).to_numpy()
    floor = initial_capital * (1 - risk.max_total_loss_pct / 100) if risk.max_total_loss_pct else None
    n = len(data)

    cash, qty = float(initial_capital), 0.0
    pos: dict | None = None
    trades: list[dict] = []
    equity = np.empty(n)
    total_fees, bars_in_market = 0.0, 0
    day, day_start, day_blocked = None, cash, False
    day_breaches, blown_at, skipped_no_stop = 0, None, 0
    daily: list[tuple[str, float, float]] = []   # (date, start equity, end equity)
    last_equity = cash

    def exit_trade(i: int, raw_px: float, reason: str, slipped: bool = True) -> None:
        nonlocal cash, qty, pos, total_fees
        side = pos["side"]
        px = raw_px * (1 - side * slip) if slipped else raw_px
        f = abs(qty) * px * fee
        total_fees += f
        cash += qty * px - f
        pnl = cash - pos["equity_before"]
        trades.append({
            "side": "long" if side > 0 else "short",
            "entry_time": pos["time"], "entry_price": round(pos["fill"], 8),
            "exit_time": times.iloc[i].isoformat(), "exit_price": round(px, 8),
            "quantity": round(abs(qty), 8), "bars_held": i - pos["index"],
            "fees": round(pos["fee"] + f, 4), "pnl": round(pnl, 4),
            "return_pct": round(pnl / pos["equity_before"] * 100, 4),
            "r_multiple": round(pnl / pos["risk_amount"], 3) if pos["risk_amount"] > 0 else None,
            "stop_price": round(pos["stop"], 8),
            "target_price": round(pos["target"], 8) if pos["target"] else None,
            "position_pct": round(pos["notional"] / pos["equity_before"] * 100, 2),
            "exit_reason": reason, "forced_exit": reason == "end_of_data",
        })
        qty, pos = 0.0, None

    for i in range(n):
        if days[i] != day:
            if day is not None:
                daily.append((str(day), day_start, last_equity))
            day, day_start, day_blocked = days[i], last_equity, False
        day_floor = (day_start * (1 - risk.daily_loss_limit_pct / 100)
                     if risk.daily_loss_limit_pct else None)

        prev_sig = sig[i - 1] if i >= 1 else np.nan
        prev2 = sig[i - 2] if i >= 2 else np.nan

        # 1) exit at the open: the signal went flat / flipped, or (events) a rule exit,
        #    an opposite entry or the time limit
        if pos is not None:
            if events is None:
                leave = prev_sig != pos["side"]
                why = "signal"
            else:
                k = i - 1
                rule = ev_xl[k] if pos["side"] > 0 else ev_xs[k]
                flip = ev_in[k] == -pos["side"]
                timed = ev_max is not None and i - pos["index"] >= ev_max
                leave = bool(rule or flip or timed)
                why = "time" if timed and not (rule or flip) else "signal"
            if leave:
                exit_trade(i, o[i], why)

        # 2) entry at the open: a fresh move of the signal into ±1, or an entry event
        if events is None:
            want = (int(prev_sig) if prev_sig in (1.0, -1.0) and np.isfinite(prev2)
                    and prev2 != prev_sig else 0)
        else:
            want = int(ev_in[i - 1]) if i >= 1 else 0
        if pos is None and blown_at is None and not day_blocked and want != 0:
            side = want
            fill = o[i] * (1 + side * slip)
            if stop_levels is not None:
                level = stop_levels[i - 1]
                dist = side * (fill - level) if np.isfinite(level) else np.nan
            elif use_pct:
                dist = fill * stop_pct
            else:
                a = atr[i - 1]
                dist = risk.stop_atr_mult * a if np.isfinite(a) else np.nan
            if not np.isfinite(dist) or dist <= 0 or (side > 0 and dist >= fill):
                skipped_no_stop += 1
            else:
                stop = fill - side * dist
                loss_per_unit = side * (fill - stop * (1 - side * slip)) + fee * (fill + stop)
                equity_before = cash if qty == 0 else cash + qty * o[i]
                risk_amount = equity_before * r
                q = risk_amount / loss_per_unit
                q = min(q, equity_before * risk.max_leverage / (fill * (1 + fee)))
                if take_profit_pct_override is not None:
                    target = fill * (1 + side * take_profit_pct_override / 100)
                elif take_profit_rr_override:
                    target = fill + side * take_profit_rr_override * dist
                elif risk.take_profit_rr:
                    target = fill + side * risk.take_profit_rr * dist
                else:
                    target = None
                entry_fee = q * fill * fee
                total_fees += entry_fee
                cash -= side * q * fill + entry_fee
                qty = side * q
                pos = {"index": i, "time": times.iloc[i].isoformat(), "fill": fill, "stop": stop,
                       "target": target, "fee": entry_fee, "equity_before": equity_before,
                       "risk_amount": q * loss_per_unit, "notional": q * fill, "side": side}

        # 3) intrabar protection: stop, daily limit, total floor. Longs lose as price falls,
        #    so the HIGHEST protective level is touched first; shorts: the LOWEST.
        if pos is not None:
            side = pos["side"]
            levels = [(pos["stop"], "stop")]
            if day_floor is not None:
                levels.append(((day_floor - cash) / qty, "daily_limit"))
            if floor is not None:
                levels.append(((floor - cash) / qty, "max_total_loss"))
            if side > 0:
                level, reason = max(levels, key=lambda x: x[0])
                hit, gap_px = lo[i] <= level, min(o[i], level)
            else:
                level, reason = min(levels, key=lambda x: x[0])
                hit, gap_px = h[i] >= level, max(o[i], level)
            tgt = pos["target"]
            if hit:
                exit_trade(i, gap_px, reason)
                if reason == "daily_limit":
                    day_blocked = True
                    day_breaches += 1
                elif reason == "max_total_loss":
                    blown_at = times.iloc[i].isoformat()
            elif tgt is not None and side > 0 and h[i] >= tgt:
                exit_trade(i, max(o[i], tgt), "target", slipped=False)
            elif tgt is not None and side < 0 and lo[i] <= tgt:
                exit_trade(i, min(o[i], tgt), "target", slipped=False)

        if pos is not None:
            bars_in_market += 1
        equity[i] = cash + qty * c[i]
        last_equity = equity[i]
        # Losses realised by a gap can breach the limits while flat.
        if day_floor is not None and last_equity <= day_floor and not day_blocked:
            day_blocked = True
            day_breaches += 1
        if floor is not None and last_equity <= floor and blown_at is None:
            blown_at = times.iloc[i].isoformat()

    if pos is not None:
        exit_trade(n - 1, c[-1], "end_of_data")
        equity[-1] = cash
        last_equity = cash
    daily.append((str(day), day_start, last_equity))

    result = summarize(times, equity, trades, total_fees, bars_in_market, initial_capital,
                       timeframe, trading_minutes_per_year)
    day_ret = np.array([e / s - 1 for _, s, e in daily if s > 0])
    worst = int(np.argmin(day_ret)) if len(day_ret) else None
    rs = np.array([t["r_multiple"] for t in trades if t["r_multiple"] is not None])
    months = [m["return_pct"] for m in result["monthly_returns_pct"]]
    result["risk"] = {
        "config": risk.__dict__,
        "worst_day_pct": round(float(day_ret[worst]) * 100, 4) if worst is not None else 0.0,
        "worst_day": daily[worst][0] if worst is not None else None,
        "daily_limit_breaches": day_breaches,
        "account_failed": blown_at is not None,
        "failed_at": blown_at,
        "lowest_equity_pct_of_initial": round(float(equity.min() / initial_capital * 100), 4),
        "avg_r_multiple": round(float(rs.mean()), 4) if len(rs) else None,
        "avg_position_pct": round(float(np.mean([t["position_pct"] for t in trades])), 2)
        if trades else 0.0,
        "avg_monthly_return_pct": round(float(np.mean(months)), 4) if months else 0.0,
        "positive_months_pct": round(float(np.mean([m > 0 for m in months]) * 100), 1)
        if months else 0.0,
        "exit_reasons": {k: sum(t["exit_reason"] == k for t in trades)
                         for k in ("signal", "time", "stop", "target", "daily_limit",
                                   "max_total_loss", "end_of_data")},
        "skipped_entries_no_stop": skipped_no_stop,
        "trading_days": len(daily),
    }
    if return_daily:
        result["daily_returns"] = day_ret
    return result
