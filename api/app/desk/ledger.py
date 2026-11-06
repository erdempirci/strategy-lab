"""Paper account of the desk.

Each strategy is backtested on its own (engine equity), so its trades carry `return_pct`
= P&L as a % of the equity it was sized from. The desk applies that % to the desk's own
realized equity at the moment of entry, which reproduces risk-based sizing on one shared
account without a second simulator.
"""

from __future__ import annotations

import pandas as pd


def _t(v) -> pd.Timestamp:
    return pd.Timestamp(v)


def build_ledger(closed: list[dict], open_: list[dict], initial: float, risk_pct: float,
                 now: pd.Timestamp) -> dict:
    """closed/open: trades with entry_time, exit_time (closed), return_pct, strategy_id.

    Returns realized and marked equity, the UTC day-start equity, per-trade P&L in
    currency and the realized equity curve.
    """
    events = []
    for k, t in enumerate(closed):
        events.append((_t(t["entry_time"]), 1, "entry", ("c", k)))
        events.append((_t(t["exit_time"]), 0, "exit", ("c", k)))   # exits first on ties
    for k, t in enumerate(open_):
        events.append((_t(t["entry_time"]), 1, "entry", ("o", k)))
    events.sort(key=lambda e: (e[0], e[1]))

    day_start_ts = now.normalize()
    realized = float(initial)
    day_start = None
    at_entry: dict = {}
    pnl: dict = {}
    curve = []
    for ts, _, kind, ref in events:
        if day_start is None and ts >= day_start_ts:
            day_start = realized
        if kind == "entry":
            at_entry[ref] = realized
        else:
            trade = closed[ref[1]]
            p = trade["return_pct"] / 100 * at_entry[ref]
            pnl[ref] = p
            realized += p
            curve.append({"time": trade["exit_time"], "equity": round(realized, 2)})
    if day_start is None:
        day_start = realized

    open_rows = []
    open_pnl = open_risk = 0.0
    for k, t in enumerate(open_):
        base = at_entry[("o", k)]
        mark = t["return_pct"] / 100 * base
        risk = base * risk_pct / 100
        open_pnl += mark
        open_risk += risk
        open_rows.append({**t, "equity_at_entry": round(base, 2), "pnl": round(mark, 2),
                          "risk_amount": round(risk, 2)})
    closed_rows = [{**t, "equity_at_entry": round(at_entry[("c", k)], 2),
                    "pnl": round(pnl[("c", k)], 2)} for k, t in enumerate(closed)]
    return {
        "initial": float(initial),
        "realized_equity": round(realized, 2),
        "open_pnl": round(open_pnl, 2),
        "equity": round(realized + open_pnl, 2),
        "day_start_equity": round(day_start, 2),
        "open_risk": round(open_risk, 2),
        "closed": closed_rows,
        "open": open_rows,
        "curve": curve,
    }


def r_stats(trades: list[dict]) -> dict:
    rs = [float(t["r_multiple"]) for t in trades if t.get("r_multiple") is not None]
    if not rs:
        return {"trades": 0, "wins": 0, "win_rate_pct": None, "profit_factor": None,
                "avg_r": None, "sum_r": 0.0}
    gains = sum(r for r in rs if r > 0)
    losses = -sum(r for r in rs if r < 0)
    wins = int(sum(r > 0 for r in rs))
    return {"trades": len(rs), "wins": wins, "win_rate_pct": round(wins / len(rs) * 100, 1),
            "profit_factor": round(gains / losses, 3) if losses > 0 else None,
            "avg_r": round(sum(rs) / len(rs), 3), "sum_r": round(sum(rs), 3)}
