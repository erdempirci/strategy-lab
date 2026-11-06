"""One desk run: Tarayıcı → Giriş-Çıkış → Risk → Şüpheci → Günlükçü.

Idempotent: running twice on the same closed bar records nothing new (decisions are
unique per strategy and signal bar, trades per strategy, kind and entry time).

Only a signal on the LAST closed bar can be approved: its entry is the next bar's open,
which the decision did not see. An older undecided signal (the desk was not running) is
journaled as GEÇ "kaçırıldı" — entering it now would use prices the decision already
knows, i.e. look-ahead.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from app.data_adapters.markets import market_of
from app.data_adapters.normalize import quality_report
from app.desk.ledger import build_ledger, r_stats
from app.desk.models import DeskSettings
from app.desk.store import DeskStore, iso
from app.services.risk_engine import RiskConfig, run_risk_backtest, wilder_atr
from app.services.timeframes import timeframe_minutes
from app.strategies.dsl import StrategySpec, custom_signal

Loader = Callable[[str, str, int], pd.DataFrame]

MAX_WINDOW = 5000
ATR_PERIOD = 14
FORWARD_MIN_TRADES = 10


def settings_of(store: DeskStore) -> DeskSettings:
    return DeskSettings(**(store.get_setting("settings") or {}))


def costs_for(symbol: str, settings: DeskSettings) -> tuple[float, float]:
    """Index futures are far cheaper to trade than crypto spot: use the market's costs."""
    m = market_of(symbol)
    return (m.fee_bps, m.slippage_bps) if m else (settings.fee_bps, settings.slippage_bps)


def volume_ratio(frame: pd.DataFrame, i: int, days: int, timeframe: str) -> float | None:
    """Bar i's volume vs. its average. Session markets compare with the same UTC hour
    (the US open always trades more than the Asian night, that is not a spike)."""
    vol = frame["volume"].to_numpy(float)
    ts = frame["timestamp"]
    since = ts.iloc[i] - pd.Timedelta(days=days)
    window = (ts >= since).to_numpy() & (np.arange(len(frame)) < i)
    if market_of_frame(frame) and timeframe_minutes(timeframe) < 1440:
        window &= (ts.dt.hour == ts.iloc[i].hour).to_numpy()
    base = vol[window]
    if len(base) < 3 or base.mean() <= 0:
        return None
    return float(vol[i] / base.mean())


def market_of_frame(frame: pd.DataFrame):
    return frame.attrs.get("market")


def closed_bars(frame: pd.DataFrame, timeframe: str, now: pd.Timestamp) -> pd.DataFrame:
    step = pd.Timedelta(minutes=timeframe_minutes(timeframe))
    f = frame.sort_values("timestamp").reset_index(drop=True)
    return f[f["timestamp"] + step <= now].reset_index(drop=True)


def _keep_runs(sig: np.ndarray, keep: set[int]) -> np.ndarray:
    """Zero every run of 1s whose first bar is not in `keep` (so it never enters)."""
    out = np.where(np.isnan(sig), np.nan, sig)
    i, n = 0, len(sig)
    while i < n:
        if sig[i] == 1.0:
            j = i
            while j < n and sig[j] == 1.0:
                j += 1
            if i not in keep:
                out[i:j] = 0.0
            i = j
        else:
            i += 1
    return out


def bars_for(days: float, timeframe: str) -> int:
    return int(np.ceil(days * 1440 / timeframe_minutes(timeframe)))


@dataclass
class StrategyEval:
    strategy: dict
    frame: pd.DataFrame | None = None
    signal: np.ndarray | None = None
    transitions: list[int] = field(default_factory=list)
    desk: list[dict] = field(default_factory=list)      # closed + open (exit_time None)
    shadow: list[dict] = field(default_factory=list)
    pending: list[int] = field(default_factory=list)    # approved, entry not yet filled
    error: str | None = None

    @property
    def last_index(self) -> int:
        return len(self.frame) - 1


def _trade_rows(strategy_id: str, kind: str, trades: list[dict], frame: pd.DataFrame) -> list[dict]:
    index_of = {iso(t): i for i, t in enumerate(frame["timestamp"])}
    rows = []
    for t in trades:
        i = index_of[iso(t["entry_time"])]
        is_open = t["exit_reason"] == "end_of_data"
        rows.append({
            "strategy_id": strategy_id, "kind": kind,
            "signal_time": iso(frame["timestamp"].iloc[i - 1]),
            "entry_time": iso(t["entry_time"]), "entry_price": t["entry_price"],
            "exit_time": None if is_open else iso(t["exit_time"]),
            "exit_price": t["exit_price"], "exit_reason": "open" if is_open else t["exit_reason"],
            "r_multiple": t["r_multiple"], "return_pct": t["return_pct"],
            "stop_price": t["stop_price"], "target_price": t["target_price"],
        })
    return rows


def evaluate(strategy: dict, frame: pd.DataFrame, settings: DeskSettings,
             approved_times: set[str]) -> StrategyEval:
    ev = StrategyEval(strategy=strategy, frame=frame)
    spec = StrategySpec.model_validate(strategy["spec"])
    tf = strategy["timeframe"]
    step = pd.Timedelta(minutes=timeframe_minutes(tf))
    sig = custom_signal(spec, frame, apply_stops=False).to_numpy(float)
    ev.signal = sig
    ts = frame["timestamp"]
    activated = pd.Timestamp(strategy["activated_at"])
    eligible = (ts + step > activated).to_numpy()
    ev.transitions = [i for i in range(1, len(sig))
                      if sig[i] == 1.0 and sig[i - 1] == 0.0 and eligible[i]]
    approved = {i for i in ev.transitions if iso(ts.iloc[i]) in approved_times}
    ev.pending = [i for i in approved if i == len(sig) - 1]

    risk = RiskConfig(risk_per_trade_pct=settings.risk_per_trade_pct, daily_loss_limit_pct=None,
                      max_total_loss_pct=None, stop_type="atr", stop_atr_period=ATR_PERIOD,
                      stop_atr_mult=float(strategy["stop_atr_mult"]),
                      take_profit_rr=strategy.get("take_profit_rr"))
    fee, slip = costs_for(strategy["symbol"], settings)
    for kind, keep in (("desk", approved), ("shadow", set(ev.transitions))):
        res = run_risk_backtest(frame, pd.Series(_keep_runs(sig, keep)), risk, tf,
                                settings.initial_capital, fee, slip)
        rows = _trade_rows(strategy["id"], kind, res["trades"], frame)
        setattr(ev, kind, rows)
    return ev


# ---------------------------------------------------------------- roles

def scanner_note(ev: StrategyEval, i: int, settings: DeskSettings) -> tuple[str, float | None]:
    f, s = ev.frame, ev.strategy
    ratio = volume_ratio(f, i, settings.volume_lookback_days, s["timeframe"])
    chg = (f["close"].iloc[i] / f["open"].iloc[i] - 1) * 100
    what = "aynı saatin " if market_of_frame(f) else ""
    vtxt = f"hacim {what}{settings.volume_lookback_days} günlük ortalamasının {ratio:.1f} katı" \
        if ratio is not None else "hacim ortalaması hesaplanamadı"
    return (f"{s['symbol']} {s['timeframe']}: \"{s['name']}\" giriş sinyali verdi "
            f"(bar {pd.Timestamp(f['timestamp'].iloc[i]).strftime('%d.%m %H:%M')} UTC). "
            f"{vtxt}, bar değişimi {chg:+.2f}%."), ratio


def plan(ev: StrategyEval, i: int, atr: np.ndarray) -> dict | None:
    s = ev.strategy
    entry = float(ev.frame["close"].iloc[i])
    a = atr[i]
    if not np.isfinite(a) or a <= 0:
        return None
    dist = float(s["stop_atr_mult"]) * a
    if dist >= entry:
        return None
    rr = s.get("take_profit_rr")
    return {"entry_ref": entry, "stop": entry - dist, "target": entry + rr * dist if rr else None,
            "stop_dist_pct": dist / entry * 100, "rr": rr}


@dataclass
class Book:
    """Running portfolio state while decisions are made within one run."""
    settings: DeskSettings
    realized: float
    equity: float
    day_start: float
    open_risk: float
    open_count: int
    open_symbols: set[str]


def risk_check(book: Book, p: dict) -> dict:
    st = book.settings
    risk_amt = book.equity * st.risk_per_trade_pct / 100
    qty = risk_amt / (p["entry_ref"] - p["stop"])
    qty = min(qty, book.equity / p["entry_ref"])
    day_floor = book.day_start * (1 - st.daily_loss_limit_pct / 100)
    total_floor = st.initial_capital * (1 - st.max_total_loss_pct / 100)
    worst = book.realized - book.open_risk - risk_amt
    room = book.realized - book.open_risk - max(day_floor, total_floor)
    base = {"risk_amount": round(risk_amt, 2), "quantity": qty,
            "notional": round(qty * p["entry_ref"], 2), "daily_room": round(room, 2),
            "equity": round(book.equity, 2)}
    if book.equity <= total_floor:
        return {**base, "status": "RED", "text": f"Toplam zarar sınırı (%{st.max_total_loss_pct:g}) "
                "aşıldı; masa durdu. Yeni işlem yok."}
    if book.equity <= day_floor:
        return {**base, "status": "RED", "text": f"Bugünkü zarar %{st.daily_loss_limit_pct:g} "
                "sınırına ulaştı; masa yarına kadar kapalı."}
    if book.open_count >= st.max_open_positions:
        return {**base, "status": "RED", "text": f"Açık pozisyon sınırı dolu "
                f"({book.open_count}/{st.max_open_positions})."}
    if worst < max(day_floor, total_floor):
        return {**base, "status": "RED", "text": "Açık pozisyonların stopları ve bu işlem "
                f"birlikte çalışırsa sınır aşılır (kalan alan {room:.2f}, gereken {risk_amt:.2f})."}
    return {**base, "status": "OK", "text": f"Risk {risk_amt:.2f} (%{st.risk_per_trade_pct:g}), "
            f"miktar {qty:.6g}, pozisyon {qty * p['entry_ref']:.2f}. Tüm stoplar çalışsa bile "
            f"günlük sınıra {room - risk_amt:.2f} kalır."}


def skeptic(ev: StrategyEval, i: int, atr: np.ndarray, book: Book, risk: dict,
            shadow_closed: list[dict]) -> tuple[str, list[str]]:
    s, f = ev.strategy, ev.frame
    if risk["status"] == "RED":
        return "GEÇ", ["Risk onay vermedi."]
    blocks, waits, notes = [], [], []
    fwd = r_stats(shadow_closed)
    proven = (fwd["trades"] >= FORWARD_MIN_TRADES and (fwd["profit_factor"] or 0) >= 1.1
              and (fwd["avg_r"] or 0) > 0)
    if fwd["trades"] >= FORWARD_MIN_TRADES and ((fwd["profit_factor"] or 0) < 0.8
                                               or (fwd["avg_r"] or 0) < -0.2):
        hold_pf = (s.get("discovery") or {}).get("holdout", {}).get("profit_factor")
        blocks.append(f"Canlı kayıtta {fwd['trades']} işlemde PF {fwd['profit_factor']}"
                      + (f", holdout'ta {hold_pf} idi" if hold_pf else "")
                      + "; strateji bozulmuş olabilir.")
    if s["verdict"] != "robust" and not proven:
        waits.append(f"Keşfet bu stratejiyi 'umut verici' buldu, şans etkisi dışlanamadı; "
                     f"canlı kayıtta {fwd['trades']}/{FORWARD_MIN_TRADES} işlem var.")
    if s["verdict"] == "failed_holdout":
        blocks.append("Strateji görmediği veride başarısız olmuştu.")
    atr_pct = atr / f["close"].to_numpy(float)
    hist = atr_pct[max(0, i - 2000):i]
    hist = hist[np.isfinite(hist)]
    if len(hist) >= 200 and atr_pct[i] > np.quantile(hist, 0.95):
        waits.append(f"Oynaklık olağandışı yüksek (ATR %{atr_pct[i] * 100:.2f}, son dönemin "
                     f"%95'lik dilimi %{np.quantile(hist, 0.95) * 100:.2f}).")
    if s["symbol"] in book.open_symbols:
        waits.append(f"{s['symbol']} için başka bir stratejinin açık pozisyonu var; aynı riski "
                     "ikiye katlamayalım.")
    m = market_of(s["symbol"])
    q = quality_report(f.iloc[max(0, i - 200):i + 1], s["timeframe"],
                       m.expected_gap if m else None)
    if q["gaps"]:
        waits.append(f"Son 200 barda {q['missing_bars_estimate']} eksik bar var; veri güvenilir değil.")
    if proven:
        notes.append(f"Canlı kayıt güçlü: {fwd['trades']} işlem, PF {fwd['profit_factor']}.")
    if blocks:
        return "GEÇ", blocks[:2]
    if waits:
        return "BEKLE", waits[:2]
    hold = (s.get("discovery") or {}).get("holdout", {})
    reason = (f"Görmediği veride {hold.get('trades')} işlemde PF {hold.get('profit_factor')}, "
              f"risk kurallarına uydu." if hold else "Kanıtlar giriş yönünde.")
    return "AL", [reason] + notes[:1]


# ---------------------------------------------------------------- run

def _load(load: Loader, symbol: str, timeframe: str, bars: int, now) -> pd.DataFrame:
    """Closed bars only. Delayed feeds (Yahoo futures) need DELAY after the bar end."""
    m = market_of(symbol)
    frame = closed_bars(load(symbol, timeframe, bars), timeframe, now - (m.delay if m else pd.Timedelta(0)))
    frame.attrs["market"] = m.symbol if m else None
    return frame


def window_bars(strategy: dict, settings: DeskSettings, now: pd.Timestamp) -> int:
    spec = StrategySpec.model_validate(strategy["spec"])
    tf = strategy["timeframe"]
    since = (now - pd.Timestamp(strategy["activated_at"])).total_seconds() / 86400
    need = max(400, 5 * spec.warmup(), bars_for(settings.volume_lookback_days, tf) + 5,
               bars_for(since, tf) + 5 * spec.warmup() + 5)
    return min(MAX_WINDOW, need)


def run_desk(store: DeskStore, load: Loader, now: pd.Timestamp | None = None,
             trigger: str = "manual") -> dict:
    now = pd.Timestamp(now) if now is not None else pd.Timestamp.now(tz="UTC")
    now = now.tz_localize("UTC") if now.tzinfo is None else now.tz_convert("UTC")
    settings = settings_of(store)
    snapshot_prev = store.get_setting("snapshot") or {}
    holding = {p["strategy_id"] for p in snapshot_prev.get("positions", [])}
    strategies = [s for s in store.list_strategies() if s["active"] or s["id"] in holding]
    decisions = store.list_decisions()
    approved_by = {}
    decided = set()
    for d in decisions:
        decided.add((d["strategy_id"], iso(d["signal_time"])))
        if d["verdict"] == "AL":
            approved_by.setdefault(d["strategy_id"], set()).add(iso(d["signal_time"]))

    errors, evals = [], []
    for s in strategies:
        try:
            frame = _load(load, s["symbol"], s["timeframe"], window_bars(s, settings, now), now)
            if len(frame) < 50:
                raise ValueError("yeterli kapanmış bar yok")
            ev = evaluate(s, frame, settings, approved_by.get(s["id"], set()))
        except Exception as exc:  # noqa: BLE001 — one broken strategy must not stop the desk
            errors.append({"strategy_id": s["id"], "name": s["name"], "error": str(exc)[:300]})
            continue
        store.add_trades([t for t in ev.desk + ev.shadow if t["exit_time"] is not None])
        evals.append(ev)

    def ledger():
        closed = [t for t in store.list_trades(kind="desk") if t["exit_time"] is not None]
        open_ = [t for ev in evals for t in ev.desk if t["exit_time"] is None]
        return build_ledger(closed, open_, settings.initial_capital, settings.risk_per_trade_pct, now)

    led = ledger()
    pending_risk = sum(len(ev.pending) for ev in evals) * led["equity"] * settings.risk_per_trade_pct / 100
    book = Book(settings=settings, realized=led["realized_equity"], equity=led["equity"],
                day_start=led["day_start_equity"], open_risk=led["open_risk"] + pending_risk,
                open_count=len(led["open"]) + sum(len(ev.pending) for ev in evals),
                open_symbols={next(e.strategy["symbol"] for e in evals if e.strategy["id"] == t["strategy_id"])
                              for t in led["open"]}
                | {ev.strategy["symbol"] for ev in evals if ev.pending})
    shadow_all = store.list_trades(kind="shadow")

    new_decisions = []
    for ev in evals:
        s = ev.strategy
        if not s["active"]:
            continue
        atr = wilder_atr(ev.frame, ATR_PERIOD).to_numpy()
        for i in ev.transitions:
            t_iso = iso(ev.frame["timestamp"].iloc[i])
            if (s["id"], t_iso) in decided:
                continue
            late = ev.last_index - i
            scan_text, vol_ratio = scanner_note(ev, i, settings)
            roles = [{"role": "Tarayıcı", "text": scan_text}]
            p = plan(ev, i, atr)
            if late >= settings.max_late_bars:
                verdict, reasons = "GEÇ", [(f"Sinyal {late} bar önce verildi, masa o sırada "
                                             "çalışmıyordu; giriş fırsatı kaçtı.")]
                risk = None
            elif p is None:
                verdict, reasons, risk = "GEÇ", ["Stop mesafesi hesaplanamadı (ATR yok)."], None
            else:
                tgt = f"hedef {p['target']:.6g} ({p['rr']:g}R)" if p["target"] else \
                    "hedef yok, çıkış sinyalle"
                roles.append({"role": "Giriş-Çıkış", "text":
                              f"Giriş sonraki bar açılışında (~{p['entry_ref']:.6g}), stop "
                              f"{p['stop']:.6g} ({s['stop_atr_mult']:g}×ATR, %{p['stop_dist_pct']:.2f}), {tgt}."})
                risk = risk_check(book, p)
                roles.append({"role": "Risk", "status": risk["status"], "text": risk["text"]})
                shadow_closed = [t for t in shadow_all if t["strategy_id"] == s["id"]
                                 and t["exit_time"] is not None]
                verdict, reasons = skeptic(ev, i, atr, book, risk, shadow_closed)
            roles.append({"role": "Şüpheci", "verdict": verdict, "text": " ".join(reasons)})
            roles.append({"role": "Günlükçü", "text":
                          "Kaydedildi. Kağıt pozisyon sonraki bar açılışında açılır."
                          if verdict == "AL" else
                          "Kaydedildi. İşlem açılmadı; gölge kayıtta sonucu izlenecek."})
            row = {"strategy_id": s["id"], "strategy_name": s["name"], "symbol": s["symbol"],
                   "timeframe": s["timeframe"], "signal_time": t_iso, "verdict": verdict,
                   "risk_status": risk["status"] if risk else None, "late_bars": late,
                   "volume_ratio": round(vol_ratio, 3) if vol_ratio is not None else None,
                   "plan": {k: round(v, 8) if isinstance(v, float) else v for k, v in p.items()}
                   if p else None,
                   "roles": roles, "trigger": trigger}
            if store.add_decision(row):
                new_decisions.append(row)
                decided.add((s["id"], t_iso))
                if verdict == "AL":
                    book.open_risk += risk["risk_amount"]
                    book.open_count += 1
                    book.open_symbols.add(s["symbol"])
                    ev.pending.append(i)

    scans = scan_watchlist(store, load, settings, now, errors)
    led = ledger()
    snapshot = make_snapshot(evals, led, settings, now, errors, trigger)
    store.set_setting("snapshot", snapshot)
    summary = {"strategies": len(evals), "new_decisions": len(new_decisions),
               "approved": sum(d["verdict"] == "AL" for d in new_decisions),
               "volume_spikes": scans, "errors": errors, "equity": led["equity"]}
    store.add_event({"kind": "run", "payload": {**summary, "trigger": trigger,
                                                 "at": iso(now)}})
    return {**summary, "decisions": new_decisions, "snapshot": snapshot}


def scan_watchlist(store: DeskStore, load: Loader, settings: DeskSettings, now, errors) -> list[dict]:
    tf = settings.scan_timeframe
    n = bars_for(settings.volume_lookback_days, tf)
    found = []
    for sym in settings.watchlist:
        try:
            f = _load(load, sym, tf, n + 2, now)
            if len(f) < n // 2:
                continue
            ratio = volume_ratio(f, len(f) - 1, settings.volume_lookback_days, tf) or 0.0
        except Exception as exc:  # noqa: BLE001 — a scan failure is reported, not fatal
            errors.append({"symbol": sym, "error": str(exc)[:300]})
            continue
        if ratio >= settings.volume_spike_x:
            bar = iso(f["timestamp"].iloc[-1])
            chg = float(f["close"].iloc[-1] / f["open"].iloc[-1] - 1) * 100
            row = {"symbol": sym, "timeframe": tf, "bar_time": bar, "volume_ratio": round(ratio, 2),
                   "change_pct": round(chg, 3),
                   "text": f"Tarayıcı: {sym} {tf} hacmi "
                           f"{'aynı saatin ' if market_of_frame(f) else ''}"
                           f"{settings.volume_lookback_days} günlük ortalamasının {ratio:.1f} katı, "
                           f"bar {chg:+.2f}%."}
            if store.add_event({"kind": "volume_spike", "dedupe_key": f"vs:{sym}:{tf}:{bar}",
                                "payload": row}):
                found.append(row)
    return found


def make_snapshot(evals: list[StrategyEval], led: dict, settings: DeskSettings, now, errors,
                  trigger) -> dict:
    by_id = {ev.strategy["id"]: ev for ev in evals}
    positions = []
    for t in led["open"]:
        ev = by_id[t["strategy_id"]]
        positions.append({"strategy_id": t["strategy_id"], "name": ev.strategy["name"],
                          "symbol": ev.strategy["symbol"], "timeframe": ev.strategy["timeframe"],
                          "status": "open", "entry_time": t["entry_time"],
                          "entry_price": t["entry_price"], "stop_price": t["stop_price"],
                          "target_price": t["target_price"], "last_price":
                          float(ev.frame["close"].iloc[-1]), "r_multiple": t["r_multiple"],
                          "pnl": t["pnl"], "risk_amount": t["risk_amount"]})
    for ev in evals:
        for i in ev.pending:
            positions.append({"strategy_id": ev.strategy["id"], "name": ev.strategy["name"],
                              "symbol": ev.strategy["symbol"], "timeframe": ev.strategy["timeframe"],
                              "status": "pending", "signal_time": iso(ev.frame["timestamp"].iloc[i]),
                              "last_price": float(ev.frame["close"].iloc[-1])})
    st = settings
    day_floor = led["day_start_equity"] * (1 - st.daily_loss_limit_pct / 100)
    total_floor = st.initial_capital * (1 - st.max_total_loss_pct / 100)
    halted = ("total" if led["equity"] <= total_floor else
              "daily" if led["equity"] <= day_floor else None)
    return {
        "at": iso(now), "trigger": trigger,
        "account": {k: led[k] for k in ("initial", "realized_equity", "open_pnl", "equity",
                                         "day_start_equity", "open_risk")}
        | {"day_pnl_pct": round((led["equity"] / led["day_start_equity"] - 1) * 100, 3),
           "total_pnl_pct": round((led["equity"] / st.initial_capital - 1) * 100, 3),
           "day_floor": round(day_floor, 2), "total_floor": round(total_floor, 2),
           "halted": halted},
        "positions": positions,
        "last_bars": {ev.strategy["id"]: iso(ev.frame["timestamp"].iloc[-1]) for ev in evals},
        "curve": led["curve"][-500:],
        "errors": errors,
    }
