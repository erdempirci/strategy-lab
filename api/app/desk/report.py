"""Günlükçü: weekly report — what was decided, what worked, what to change."""

from __future__ import annotations

import pandas as pd

from app.desk.engine import FORWARD_MIN_TRADES, settings_of
from app.desk.ledger import build_ledger, r_stats
from app.desk.store import DeskStore, iso


def weekly_report(store: DeskStore, days: int = 7, now: pd.Timestamp | None = None) -> dict:
    now = pd.Timestamp(now) if now is not None else pd.Timestamp.now(tz="UTC")
    since = now - pd.Timedelta(days=days)
    settings = settings_of(store)
    strategies = {s["id"]: s for s in store.list_strategies()}
    decisions = store.list_decisions(since=iso(since))
    desk_all = [t for t in store.list_trades(kind="desk") if t["exit_time"]]
    shadow_all = [t for t in store.list_trades(kind="shadow") if t["exit_time"]]
    led = build_ledger(desk_all, [], settings.initial_capital, settings.risk_per_trade_pct, now)
    in_period = [t for t in led["closed"] if pd.Timestamp(t["exit_time"]) >= since]

    verdicts = {v: sum(d["verdict"] == v for d in decisions) for v in ("AL", "BEKLE", "GEÇ")}
    missed = sum((d.get("late_bars") or 0) > 0 and d["verdict"] == "GEÇ" for d in decisions)
    red = sum(d.get("risk_status") == "RED" for d in decisions)

    shadow_by_signal = {(t["strategy_id"], iso(t["signal_time"])): t for t in shadow_all}
    blocked = []
    for d in decisions:
        if d["verdict"] == "AL":
            continue
        t = shadow_by_signal.get((d["strategy_id"], iso(d["signal_time"])))
        if t is not None:
            blocked.append({"strategy": d.get("strategy_name"), "signal_time": d["signal_time"],
                            "verdict": d["verdict"], "r_multiple": t["r_multiple"]})
    b = r_stats(blocked)

    per_strategy = []
    suggestions = []
    for sid, s in strategies.items():
        fwd = r_stats([t for t in shadow_all if t["strategy_id"] == sid])
        desk = r_stats([t for t in desk_all if t["strategy_id"] == sid])
        hold = (s.get("discovery") or {}).get("holdout", {})
        per_strategy.append({"id": sid, "name": s["name"], "symbol": s["symbol"],
                             "timeframe": s["timeframe"], "verdict": s["verdict"],
                             "active": s["active"], "desk": desk, "forward": fwd,
                             "holdout": {"trades": hold.get("trades"),
                                         "profit_factor": hold.get("profit_factor"),
                                         "avg_r": hold.get("avg_r")}})
        if not s["active"]:
            continue
        if fwd["trades"] >= FORWARD_MIN_TRADES and (fwd["profit_factor"] or 0) < 0.8:
            suggestions.append(f"\"{s['name']}\" canlıda {fwd['trades']} işlemde PF "
                               f"{fwd['profit_factor']}; masadan çıkarmayı düşün.")
        elif (s["verdict"] == "promising" and fwd["trades"] >= FORWARD_MIN_TRADES
              and (fwd["profit_factor"] or 0) >= 1.1):
            suggestions.append(f"\"{s['name']}\" canlıda kendini kanıtladı ({fwd['trades']} işlem, "
                               f"PF {fwd['profit_factor']}); Şüpheci artık AL verebiliyor.")
        elif hold.get("avg_r") is not None and fwd["trades"] >= FORWARD_MIN_TRADES \
                and (fwd["avg_r"] or 0) < hold["avg_r"] / 2:
            suggestions.append(f"\"{s['name']}\" canlıda holdout'un yarısından az kazanıyor "
                               f"(ort. {fwd['avg_r']}R vs {hold['avg_r']}R); izlemeye devam.")
    if b["trades"]:
        if b["sum_r"] < 0:
            suggestions.append(f"Şüpheci'nin durdurduğu {b['trades']} işlem toplam {b['sum_r']}R "
                               "kaybettirirdi; itirazları işe yarıyor.")
        elif b["sum_r"] > 0:
            suggestions.append(f"Şüpheci'nin durdurduğu {b['trades']} işlem toplam +{b['sum_r']}R "
                               "kazandırırdı; kuralları fazla sıkı olabilir, örneklem büyüdükçe bak.")
    if missed:
        suggestions.append(f"{missed} sinyal masa çalışmadığı için kaçtı; zamanlayıcıyı kontrol et.")
    runs = [e for e in store.list_events(kind="run", limit=500)
            if pd.Timestamp(e["created_at"]) >= since]
    run_errors = sum(len(e["payload"].get("errors") or []) for e in runs)
    if run_errors:
        suggestions.append(f"Çalışmalarda {run_errors} veri/strateji hatası oldu; masa durumunu incele.")
    if not decisions:
        suggestions.append("Bu dönemde hiç sinyal gelmedi. Masadaki stratejiler seyrek işlem "
                           "açıyor olabilir; Keşfet'ten başka sembol/zaman dilimi ekleyebilirsin.")

    week_pnl = sum(t["pnl"] for t in in_period)
    return {
        "period": {"start": iso(since), "end": iso(now), "days": days},
        "runs": len(runs), "expected_runs": days * 24,
        "decisions": {"total": len(decisions), **verdicts, "risk_red": red, "missed": missed},
        "desk": {**r_stats(in_period), "pnl": round(week_pnl, 2),
                 "equity": led["realized_equity"]},
        "skeptic_audit": {**b, "items": blocked[-20:]},
        "strategies": per_strategy,
        "suggestions": suggestions,
    }
