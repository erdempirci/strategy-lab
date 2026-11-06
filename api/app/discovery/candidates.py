"""Candidate strategy generator for the discovery engine.

Every candidate is a plain DSL spec (the same format users and the AI write) plus
stop/target settings for the risk engine. Generation is seeded and reproducible.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from app.strategies.dsl import StrategySpec


@dataclass
class Candidate:
    id: str
    family: str
    spec: StrategySpec
    stop_atr_mult: float
    take_profit_rr: float | None


def _c(left, op, right):
    return {"left": str(left), "op": op, "right": str(right)}


def _trend_pullback(r):
    t, p, lvl = r.choice([50, 100, 200]), r.choice([7, 14]), r.choice([30, 35, 40, 50])
    ind = [{"id": f"ema{t}", "type": "ema", "period": t}, {"id": f"rsi{p}", "type": "rsi", "period": p}]
    entry = [_c("close", ">", f"ema{t}"), _c(f"rsi{p}", "crosses_above", lvl)]
    exit_ = [_c(f"rsi{p}", ">=", r.choice([65, 70, 75]))]
    return f"Trend + RSI dönüşü (EMA{t}, RSI{p}>{lvl})", ind, entry, exit_


def _ema_cross(r):
    f, s = r.choice([(9, 21), (12, 26), (20, 50), (50, 200)])
    ind = [{"id": f"ef{f}", "type": "ema", "period": f}, {"id": f"es{s}", "type": "ema", "period": s}]
    return (f"EMA {f}/{s} kesişimi", ind, [_c(f"ef{f}", "crosses_above", f"es{s}")],
            [_c(f"ef{f}", "crosses_below", f"es{s}")])


def _donchian(r):
    n, m = r.choice([20, 40, 55]), r.choice([10, 20])
    ind = [{"id": f"hh{n}", "type": "highest", "period": n}, {"id": f"ll{m}", "type": "lowest", "period": m}]
    return (f"Donchian {n}/{m} kırılımı", ind, [_c("close", ">", f"hh{n}[1]")],
            [_c("close", "<", f"ll{m}[1]")])


def _bb_reversion(r):
    p, sd = r.choice([20, 30]), r.choice([2.0, 2.5])
    ind = [{"id": "bb", "type": "bb", "period": p, "std": sd}]
    entry = [_c("close[1]", "<", "bb.lower[1]"), _c("close", ">", "bb.lower")]
    return f"Bollinger({p},{sd:g}) dönüşü", ind, entry, [_c("close", ">=", "bb.middle")]


def _bb_breakout(r):
    p, sd = r.choice([20, 30]), r.choice([1.5, 2.0])
    ind = [{"id": "bb", "type": "bb", "period": p, "std": sd}]
    return (f"Bollinger({p},{sd:g}) kırılımı", ind, [_c("close", "crosses_above", "bb.upper")],
            [_c("close", "<", "bb.middle")])


def _macd(r):
    fast, slow, sig = r.choice([(12, 26, 9), (8, 21, 5)])
    ind = [{"id": "m", "type": "macd", "fast": fast, "slow": slow, "signal": sig}]
    return (f"MACD({fast},{slow},{sig}) momentum", ind, [_c("m.hist", "crosses_above", 0)],
            [_c("m.hist", "crosses_below", 0)])


def _rsi_momentum(r):
    p, up = r.choice([14, 21]), r.choice([55, 60])
    ind = [{"id": f"rsi{p}", "type": "rsi", "period": p}]
    return (f"RSI{p} momentum >{up}", ind, [_c(f"rsi{p}", "crosses_above", up)],
            [_c(f"rsi{p}", "<", 100 - up)])


def _roc(r):
    n, x = r.choice([12, 24, 48]), r.choice([1, 2, 3])
    ind = [{"id": f"roc{n}", "type": "roc", "period": n}]
    return (f"ROC{n} > %{x}", ind, [_c(f"roc{n}", "crosses_above", x)], [_c(f"roc{n}", "<", 0)])


# previous bar red, this body covers it: open <= close[1] < open[1] < close (green implied)
BULL_ENGULF = [_c("close[1]", "<", "open[1]"), _c("open", "<=", "close[1]"),
               _c("close", ">", "open[1]")]
BEAR_ENGULF = [_c("close[1]", ">", "open[1]"), _c("open", ">=", "close[1]"),
               _c("close", "<", "open[1]")]


def _engulfing(r):
    """Bullish engulfing: a green body that swallows the previous red body, with context."""
    ctx = r.choice(["trend", "sweep", "rsi", "plain"])
    ind, entry, label = [], list(BULL_ENGULF), "Yutan boğa mumu"
    if ctx == "trend":
        t = r.choice([50, 100, 200])
        ind.append({"id": f"ema{t}", "type": "ema", "period": t})
        entry.append(_c("close", ">", f"ema{t}"))
        label += f" · trend (EMA{t} üstü)"
    elif ctx == "sweep":
        n = r.choice([10, 20, 50])
        ind.append({"id": f"ll{n}", "type": "lowest", "period": n, "source": "low"})
        entry.append(_c("low", "<", f"ll{n}[1]"))
        label += f" · {n} bar dibini süpürüp dönüş"
    elif ctx == "rsi":
        lvl = r.choice([35, 40, 45])
        ind.append({"id": "rsi14", "type": "rsi", "period": 14})
        entry.append(_c("rsi14[1]", "<", lvl))
        label += f" · geri çekilme (RSI14 < {lvl})"
    return label, ind, entry, list(BEAR_ENGULF)


FAMILIES = {"trend_pullback": _trend_pullback, "ema_cross": _ema_cross, "donchian": _donchian,
            "bb_reversion": _bb_reversion, "bb_breakout": _bb_breakout, "macd": _macd,
            "rsi_momentum": _rsi_momentum, "roc": _roc, "engulfing": _engulfing}
FAMILY_LABELS = {"trend_pullback": "Trend + RSI dönüşü", "ema_cross": "EMA kesişimi",
                 "donchian": "Donchian kırılımı", "bb_reversion": "Bollinger dönüşü",
                 "bb_breakout": "Bollinger kırılımı", "macd": "MACD momentum",
                 "rsi_momentum": "RSI momentum", "roc": "ROC momentum",
                 "engulfing": "Yutan mum (engulfing)"}

SESSIONS = [None, None, (7, 16, "Londra"), (13, 21, "New York"), (0, 8, "Asya")]


def generate(n: int, seed: int = 42, intraday: bool = True,
             families: list[str] | None = None) -> list[Candidate]:
    r = random.Random(seed)
    pool = [f for f in FAMILIES if not families or f in families]
    if not pool:
        raise ValueError(f"unknown families: {families}")
    out, seen = [], set()
    attempts = 0
    while len(out) < n and attempts < n * 20:
        attempts += 1
        family = r.choice(pool)
        name, ind, entry, exit_ = FAMILIES[family](r)
        ind = [dict(i) for i in ind]
        entry = list(entry)
        tags = []
        if r.random() < 0.35 and not any(i["id"] == "ema200" for i in ind):
            ind.append({"id": "trend200", "type": "ema", "period": 200})
            entry.append(_c("close", ">", "trend200"))
            tags.append("EMA200 filtresi")
        session = r.choice(SESSIONS) if intraday else None
        if session:
            a, b, label = session
            entry += [_c("hour", ">=", a), _c("hour", "<", b)]
            tags.append(f"{label} seansı")
        if r.random() < 0.2:
            entry.append(_c("weekday", "<", 5))
            tags.append("hafta içi")
        use_signal_exit = r.random() < 0.7
        max_bars = None if use_signal_exit else r.choice([12, 24, 48, 96])
        stop = r.choice([1.5, 2.0, 3.0])
        rr = r.choice([1.5, 2.0, 3.0, None])
        if not use_signal_exit and rr is None:
            rr = 2.0
        key = (name, tuple(tags), use_signal_exit, max_bars, stop, rr)
        if key in seen:
            continue
        seen.add(key)
        full = name + (" · " + ", ".join(tags) if tags else "")
        spec = StrategySpec.model_validate({
            "name": full[:80], "indicators": ind, "entry": {"all": entry},
            "exit": {"all": exit_} if use_signal_exit else {},
            "max_bars_in_trade": max_bars,
        })
        out.append(Candidate(f"c{len(out) + 1:03d}", family, spec, stop, rr))
    return out
