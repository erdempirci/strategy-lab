"""Strategies from Quantified Strategies' video "6 RSI Trading Strategies Tested"
(Oct 2026), transcribed rule by rule. Defined in the DSL, so they run through exactly
the same validated evaluator as user strategies.

Video reference results are for SPY, daily bars, ~1993–2026, no costs, fills typically
at the close. Our engine fills at the NEXT open and charges costs, so expect differences.
"""

from __future__ import annotations

from app.strategies.base import Overlay, Strategy
from app.strategies.dsl import StrategySpec, custom_signal, overlay_lines


def _c(left, op, right):
    return {"left": str(left), "op": op, "right": str(right)}


QS_EXIT = _c("close", ">", "high[1]")       # "today's close above yesterday's high"
DECLINED_3 = lambda x: [_c(x, "<", f"{x}[1]"), _c(f"{x}[1]", "<", f"{x}[2]"),
                        _c(f"{x}[2]", "<", f"{x}[3]")]

PRESETS: list[dict] = [
    {
        "id": "qs_rsi2", "name": "RSI(2) Klasik",
        "note": "RSI(2) < 10 al, RSI(2) > 80 sat. Video (SPY): ~%9/yıl, zamanın %27'si pozisyonda.",
        "spec": {"indicators": [{"id": "rsi2", "type": "rsi", "period": 2}],
                 "entry": {"all": [_c("rsi2", "<", 10)]},
                 "exit": {"all": [_c("rsi2", ">", 80)]}},
    },
    {
        "id": "qs_rsi_drop", "name": "RSI Drop",
        "note": "Kapanış > SMA200, RSI(2) 3 gün üst üste düşmüş ve < 20 → al; RSI(2) > 70 sat. "
                "Video (SPY): max DD ~%14, zamanın %12'si pozisyonda.",
        "spec": {"indicators": [{"id": "sma200", "type": "sma", "period": 200},
                                {"id": "rsi2", "type": "rsi", "period": 2}],
                 "entry": {"all": [_c("close", ">", "sma200"), *DECLINED_3("rsi2"),
                                   _c("rsi2", "<", 20)]},
                 "exit": {"all": [_c("rsi2", ">", 70)]}},
    },
    {
        "id": "qs_triple_rsi", "name": "Triple RSI",
        "note": "RSI(5) < 30, 3 gün üst üste düşmüş, 3 gün önce > 60, kapanış > SMA200 → al. "
                "Çıkış videoda söylenmiyor; kanalın yayımladığı versiyondaki RSI(5) > 50 kullanıldı. "
                "Video (SPY): sadece 91 işlem.",
        "spec": {"indicators": [{"id": "sma200", "type": "sma", "period": 200},
                                {"id": "rsi5", "type": "rsi", "period": 5}],
                 "entry": {"all": [_c("rsi5", "<", 30), *DECLINED_3("rsi5"),
                                   _c("rsi5[3]", ">", 60), _c("close", ">", "sma200")]},
                 "exit": {"all": [_c("rsi5", ">", 50)]}},
    },
    {
        "id": "qs_normalized_rsi", "name": "Normalized RSI",
        "note": "RSI(2)'nin son 50 gündeki aralığına göre konumu < 0,15 ve kapanış > SMA200 → al; "
                "QS exit (kapanış > dünkü en yüksek). Video (SPY): 426 işlem, ~%6,1/yıl, %18 pozisyonda.",
        "spec": {"indicators": [{"id": "sma200", "type": "sma", "period": 200},
                                {"id": "rsi2", "type": "rsi", "period": 2},
                                {"id": "nrsi", "type": "normalize", "period": 50, "of": "rsi2"}],
                 "entry": {"all": [_c("nrsi", "<", 0.15), _c("close", ">", "sma200")]},
                 "exit": {"all": [QS_EXIT]}},
    },
    {
        "id": "qs_rsi_momentum", "name": "RSI Momentum (bull range)",
        "note": "RSI(14) son 100 günde hiç 40 altına inmemiş VE en az bir kez 70'i geçmiş → al; "
                "iki koşul da bozulunca sat. Video (SPY): zamanın %37'si pozisyonda, al-tut'u geçmiyor.",
        "spec": {"indicators": [{"id": "rsi14", "type": "rsi", "period": 14},
                                {"id": "rsi_lo", "type": "lowest", "period": 100, "of": "rsi14"},
                                {"id": "rsi_hi", "type": "highest", "period": 100, "of": "rsi14"}],
                 "entry": {"all": [_c("rsi_lo", ">", 40), _c("rsi_hi", ">", 70)]},
                 "exit": {"all": [_c("rsi_lo", "<=", 40), _c("rsi_hi", "<=", 70)]}},
    },
    {
        "id": "qs_rsi_pullback", "name": "RSI Pullback",
        "note": "Kapanış > SMA100, kapanış < SMA5, RSI(5) < 30 → al; QS exit veya 10 gün sonra çık. "
                "Video (SPY): 184 işlem.",
        "spec": {"indicators": [{"id": "sma100", "type": "sma", "period": 100},
                                {"id": "sma5", "type": "sma", "period": 5},
                                {"id": "rsi5", "type": "rsi", "period": 5}],
                 "entry": {"all": [_c("close", ">", "sma100"), _c("close", "<", "sma5"),
                                   _c("rsi5", "<", 30)]},
                 "exit": {"all": [QS_EXIT]},
                 "max_bars_in_trade": 10},
    },
]


def _strategy(p: dict) -> Strategy:
    spec = StrategySpec.model_validate({"name": p["name"], **p["spec"]})

    def signal(frame):
        return custom_signal(spec, frame)

    def overlays(frame, **_):
        return [Overlay(o.name, o.values, None) for o in overlay_lines(spec, frame)]

    return Strategy(p["id"], f"{p['name']} (QS video)", p["note"] + " En iyi günlük (1d) grafikte.",
                    (), signal, warmup=lambda _p: spec.warmup(), overlays=overlays,
                    category="video")


PRESET_STRATEGIES = {p["id"]: _strategy(p) for p in PRESETS}
PRESET_SPECS = {p["id"]: StrategySpec.model_validate({"name": p["name"], **p["spec"]})
                for p in PRESETS}
