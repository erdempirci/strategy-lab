"""Declarative strategy DSL — the only way user/AI strategies reach the engine.

Nothing here executes code. A spec is JSON validated by pydantic, then evaluated
with a fixed set of indicators and comparison operators.

Operands are strings:
    "close", "open", "high", "low", "volume", "hour" (UTC 0-23), "weekday" (0=Mon)
    an indicator id, e.g. "ema50", or an output, e.g. "bb.upper", "macd1.hist"
    any of the above with a look-back, e.g. "close[1]" = previous bar's close
    a number, e.g. "30" or "-0.5"

Every value at bar i uses only bars 0..i. Look-backs are non-negative by grammar.
Stops/targets are CLOSE-based: evaluated on each bar close against the actual
fill price (next-bar open), and exit at the following open like any other signal.
"""

from __future__ import annotations

import re
from typing import Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel, Field, field_validator, model_validator

from app.strategies.library import ema, rsi

PRICE_FIELDS = {"open", "high", "low", "close", "volume"}
# Candle anatomy, derived from OHLC of the same bar.
CANDLE_FIELDS = {"body", "upper_wick", "lower_wick", "range"}
TIME_FIELDS = {"hour", "weekday"}
OUTPUTS = {"macd": {"line", "signal", "hist"}, "bb": {"upper", "middle", "lower"}}
OPERAND_RE = re.compile(r"^(?P<name>[a-z][a-z0-9_]*(?:\.[a-z]+)?)(?:\[(?P<shift>\d{1,3})\])?$")
NUMBER_RE = re.compile(r"^-?\d+(\.\d+)?$")
ID_RE = r"^[a-z][a-z0-9_]{0,23}$"
MAX_PERIOD = 500

IndicatorType = Literal["sma", "ema", "rsi", "macd", "bb", "atr", "highest", "lowest", "roc",
                        "normalize"]
OF_TYPES = {"sma", "ema", "highest", "lowest", "normalize"}
OF_RE = r"^[a-z][a-z0-9_]{0,23}(\.[a-z]+)?$"
Op = Literal[">", "<", ">=", "<=", "crosses_above", "crosses_below"]


class Indicator(BaseModel):
    id: str = Field(pattern=ID_RE)
    type: IndicatorType
    period: int = Field(default=14, ge=1, le=MAX_PERIOD)
    source: Literal["open", "high", "low", "close", "volume"] = "close"
    # apply to another (earlier) indicator instead of a price, e.g. highest of rsi14
    of: str | None = Field(default=None, pattern=OF_RE)
    # macd
    fast: int = Field(default=12, ge=1, le=MAX_PERIOD)
    slow: int = Field(default=26, ge=2, le=MAX_PERIOD)
    signal: int = Field(default=9, ge=1, le=MAX_PERIOD)
    # bollinger
    std: float = Field(default=2.0, gt=0, le=5)

    @field_validator("id")
    @classmethod
    def _not_reserved(cls, v: str) -> str:
        if v in PRICE_FIELDS | TIME_FIELDS | CANDLE_FIELDS:
            raise ValueError(f"'{v}' is a reserved name")
        return v

    @model_validator(mode="after")
    def _macd_order(self) -> Indicator:
        if self.type == "macd" and self.fast >= self.slow:
            raise ValueError(f"{self.id}: macd fast must be < slow")
        if self.of is not None and self.type not in OF_TYPES:
            raise ValueError(f"{self.id}: 'of' works with {', '.join(sorted(OF_TYPES))}")
        return self

    def warmup(self) -> int:
        if self.type == "macd":
            return self.slow + self.signal
        return self.period + 1


class Condition(BaseModel):
    left: str
    op: Op
    right: str
    # Compare against right × right_mult, e.g. lower_wick > body × 2.
    right_mult: float = Field(default=1.0, gt=0, le=1000)

    @field_validator("left", "right", mode="before")
    @classmethod
    def _to_str(cls, v):
        return str(v).strip() if isinstance(v, int | float) else str(v).strip().lower()


class Rules(BaseModel):
    all: list[Condition] = Field(default_factory=list, max_length=8)
    any: list[Condition] = Field(default_factory=list, max_length=8)


class StrategySpec(BaseModel):
    name: str = Field(default="Özel strateji", max_length=80)
    indicators: list[Indicator] = Field(default_factory=list, max_length=12)
    entry: Rules
    exit: Rules = Field(default_factory=Rules)
    stop_loss_pct: float | None = Field(default=None, gt=0, le=50)
    take_profit_pct: float | None = Field(default=None, gt=0, le=500)
    max_bars_in_trade: int | None = Field(default=None, ge=1, le=5000)
    # Structural stop: price level read at the SIGNAL bar's close, e.g. "low" (the signal
    # candle's low = its wick), "low[1]", or an indicator such as a 10-bar lowest low.
    stop_at: str | None = Field(default=None, max_length=40)
    # Target as a multiple of the actual risk (entry − stop), e.g. 1.5 for 1:1.5.
    take_profit_rr: float | None = Field(default=None, gt=0, le=20)

    @model_validator(mode="after")
    def _check_references(self) -> StrategySpec:
        ids = [i.id for i in self.indicators]
        if len(ids) != len(set(ids)):
            raise ValueError("indicator ids must be unique")
        types: dict[str, str] = {}
        for ind in self.indicators:
            if ind.of is not None:
                base = ind.of.split(".")[0]
                if base not in types:
                    raise ValueError(f"{ind.id}: 'of' must name an indicator declared before it")
                _parse_operand(ind.of, types)
            types[ind.id] = ind.type
        if not (self.entry.all or self.entry.any):
            raise ValueError("entry needs at least one condition")
        if self.stop_at is not None:
            self.stop_at = self.stop_at.strip().lower()
            parsed = _parse_operand(self.stop_at, types)
            if isinstance(parsed, float):
                raise ValueError("stop_at must be a price level (e.g. 'low'), not a number")
            if self.stop_loss_pct:
                raise ValueError("use either stop_at or stop_loss_pct, not both")
        if self.take_profit_rr and self.take_profit_pct:
            raise ValueError("use either take_profit_rr or take_profit_pct, not both")
        if self.take_profit_rr and not (self.stop_at or self.stop_loss_pct):
            raise ValueError("take_profit_rr needs a stop (stop_at or stop_loss_pct)")
        has_exit = (self.exit.all or self.exit.any or self.stop_loss_pct or self.stop_at
                    or self.take_profit_pct or self.max_bars_in_trade)
        if not has_exit:
            raise ValueError("add at least one exit: exit condition, stop, target or max bars")
        for cond in self.conditions():
            for side in (cond.left, cond.right):
                _parse_operand(side, types)
        return self

    def conditions(self) -> list[Condition]:
        return [*self.entry.all, *self.entry.any, *self.exit.all, *self.exit.any]

    def warmup(self) -> int:
        chain: dict[str, int] = {}
        for i in self.indicators:
            chain[i.id] = i.warmup() + (chain[i.of.split(".")[0]] if i.of else 0)
        base = max(chain.values(), default=0)
        shifts = []
        sides = [(c.left, c.right) for c in self.conditions()]
        if self.stop_at:
            sides.append((self.stop_at, "0"))
        for pair in sides:
            for side in pair:
                m = OPERAND_RE.match(side)
                if m and m.group("shift"):
                    shifts.append(int(m.group("shift")))
        return base + max(shifts, default=0)


def _parse_operand(text: str, types: dict[str, str]) -> tuple[str, str | None, int] | float:
    """Return a number, or (name, output, shift). Raises ValueError if invalid."""
    if NUMBER_RE.match(text):
        return float(text)
    m = OPERAND_RE.match(text)
    if not m:
        raise ValueError(f"invalid operand {text!r}")
    name, shift = m.group("name"), int(m.group("shift") or 0)
    base, _, output = name.partition(".")
    output = output or None
    if base in PRICE_FIELDS | TIME_FIELDS | CANDLE_FIELDS:
        if output:
            raise ValueError(f"{text!r}: '{base}' has no outputs")
        return base, None, shift
    if base not in types:
        raise ValueError(f"unknown operand {text!r} — declare it in indicators")
    allowed = OUTPUTS.get(types[base])
    if allowed and output not in allowed:
        raise ValueError(f"{text!r}: {types[base]} needs one of .{', .'.join(sorted(allowed))}")
    if not allowed and output:
        raise ValueError(f"{text!r}: {types[base]} has no outputs")
    return base, output, shift


# ---------------------------------------------------------------- evaluation
def _indicator_series(ind: Indicator, frame: pd.DataFrame,
                      done: dict[str, dict[str | None, pd.Series]] | None = None,
                      ) -> dict[str | None, pd.Series]:
    if ind.of is not None:
        base, _, output = ind.of.partition(".")
        src = done[base][output or None]
        if ind.type == "highest":
            return {None: src.rolling(ind.period).max()}
        if ind.type == "lowest":
            return {None: src.rolling(ind.period).min()}
    else:
        src = frame[ind.source]
    if ind.type == "normalize":
        lo, hi = src.rolling(ind.period).min(), src.rolling(ind.period).max()
        rng = hi - lo
        out = (src - lo) / rng.where(rng > 0)
        return {None: out.where(rng > 0, 0.5).where(lo.notna())}
    if ind.type == "sma":
        return {None: src.rolling(ind.period).mean()}
    if ind.type == "ema":
        out = ema(src, ind.period)
        out.iloc[: ind.period - 1] = np.nan
        return {None: out}
    if ind.type == "rsi":
        return {None: rsi(src, ind.period)}
    if ind.type == "roc":
        return {None: src.pct_change(ind.period) * 100}
    if ind.type == "highest":
        return {None: frame["high" if ind.source == "close" else ind.source].rolling(ind.period).max()}
    if ind.type == "lowest":
        return {None: frame["low" if ind.source == "close" else ind.source].rolling(ind.period).min()}
    if ind.type == "atr":
        prev = frame["close"].shift(1)
        tr = pd.concat([frame["high"] - frame["low"], (frame["high"] - prev).abs(),
                        (frame["low"] - prev).abs()], axis=1).max(axis=1)
        out = tr.ewm(alpha=1 / ind.period, adjust=False).mean()
        out.iloc[: ind.period] = np.nan
        return {None: out}
    if ind.type == "macd":
        line = ema(src, ind.fast) - ema(src, ind.slow)
        sig = ema(line, ind.signal)
        warm = ind.slow + ind.signal
        series = {"line": line, "signal": sig, "hist": line - sig}
        for s in series.values():
            s.iloc[:warm] = np.nan
        return series
    if ind.type == "bb":
        mid = src.rolling(ind.period).mean()
        sd = src.rolling(ind.period).std(ddof=0)
        return {"upper": mid + ind.std * sd, "middle": mid, "lower": mid - ind.std * sd}
    raise ValueError(ind.type)


class _Context:
    def __init__(self, spec: StrategySpec, frame: pd.DataFrame):
        self.frame = frame.reset_index(drop=True)
        self.types = {i.id: i.type for i in spec.indicators}
        self.series: dict[str, dict[str | None, pd.Series]] = {}
        for i in spec.indicators:          # in order: 'of' may use earlier indicators
            self.series[i.id] = _indicator_series(i, self.frame, self.series)
        ts = pd.to_datetime(self.frame["timestamp"], utc=True)
        self.time = {"hour": ts.dt.hour.astype(float), "weekday": ts.dt.weekday.astype(float)}

    def operand(self, text: str) -> pd.Series:
        parsed = _parse_operand(text, self.types)
        if isinstance(parsed, float):
            return pd.Series(parsed, index=self.frame.index)
        name, output, shift = parsed
        if name in PRICE_FIELDS:
            s = self.frame[name].astype(float)
        elif name in CANDLE_FIELDS:
            f = self.frame
            top, bottom = f[["open", "close"]].max(axis=1), f[["open", "close"]].min(axis=1)
            s = {"body": top - bottom, "upper_wick": f["high"] - top,
                 "lower_wick": bottom - f["low"], "range": f["high"] - f["low"]}[name].astype(float)
        elif name in TIME_FIELDS:
            s = self.time[name]
        else:
            s = self.series[name][output]
        return s.shift(shift) if shift else s

    def condition(self, c: Condition) -> tuple[pd.Series, pd.Series]:
        """(boolean result, validity mask)."""
        a, b = self.operand(c.left), self.operand(c.right) * c.right_mult
        valid = a.notna() & b.notna()
        if c.op in ("crosses_above", "crosses_below"):
            pa, pb = a.shift(1), b.shift(1)
            valid &= pa.notna() & pb.notna()
            res = (a > b) & (pa <= pb) if c.op == "crosses_above" else (a < b) & (pa >= pb)
        else:
            res = {">": a > b, "<": a < b, ">=": a >= b, "<=": a <= b}[c.op]
        return res.fillna(False), valid

    def rules(self, r: Rules) -> tuple[pd.Series, pd.Series]:
        idx = self.frame.index
        valid = pd.Series(True, index=idx)
        res = pd.Series(True, index=idx)
        for c in r.all:
            v, ok = self.condition(c)
            res &= v
            valid &= ok
        if r.any:
            anyres = pd.Series(False, index=idx)
            for c in r.any:
                v, ok = self.condition(c)
                anyres |= v
                valid &= ok
            res &= anyres
        if not (r.all or r.any):
            res = pd.Series(False, index=idx)
        return res, valid


def custom_signal(spec: StrategySpec, frame: pd.DataFrame, apply_stops: bool = True) -> pd.Series:
    """apply_stops=False leaves stop/target to the risk engine (intrabar)."""
    ctx = _Context(spec, frame)
    enter, v_enter = ctx.rules(spec.entry)
    exit_, v_exit = ctx.rules(spec.exit)
    enter, exit_ = enter.to_numpy(), exit_.to_numpy()
    valid = (v_enter & (v_exit if (spec.exit.all or spec.exit.any) else True)).to_numpy()
    warm = spec.warmup()
    opens = ctx.frame["open"].to_numpy(float)
    closes = ctx.frame["close"].to_numpy(float)
    sl = spec.stop_loss_pct / 100 if spec.stop_loss_pct and apply_stops else None
    tp = spec.take_profit_pct / 100 if spec.take_profit_pct and apply_stops else None
    levels = stop_levels(spec, ctx.frame, ctx) if apply_stops else None
    rr = spec.take_profit_rr if apply_stops else None

    out = np.full(len(frame), np.nan)
    pos, entry_bar, entry_px = 0.0, -1, np.nan
    stop_px = tgt_px = np.nan
    pending = np.nan
    for i in range(len(frame)):
        if pos == 1.0 and i == entry_bar:
            entry_px = opens[i]          # actual fill: this bar's open, known now
            if levels is not None:
                stop_px = pending
            elif sl is not None:
                stop_px = entry_px * (1 - sl)
            if tp is not None:
                tgt_px = entry_px * (1 + tp)
            elif rr is not None and np.isfinite(stop_px) and stop_px < entry_px:
                tgt_px = entry_px + rr * (entry_px - stop_px)
        if pos == 0.0 and (i < warm or not valid[i]):
            continue
        if pos == 0.0:
            if i >= warm and valid[i] and enter[i] and (levels is None or np.isfinite(levels[i])):
                pos, entry_bar = 1.0, i + 1
                pending = levels[i] if levels is not None else np.nan
        else:
            in_trade = i >= entry_bar
            hit_sl = in_trade and np.isfinite(stop_px) and closes[i] <= stop_px
            hit_tp = in_trade and np.isfinite(tgt_px) and closes[i] >= tgt_px
            timed = (in_trade and spec.max_bars_in_trade is not None
                     and i - entry_bar + 1 >= spec.max_bars_in_trade)
            if (valid[i] and exit_[i]) or hit_sl or hit_tp or timed:
                pos, entry_px, stop_px, tgt_px = 0.0, np.nan, np.nan, np.nan
        out[i] = pos
    return pd.Series(out)


def stop_levels(spec: StrategySpec, frame: pd.DataFrame, ctx: _Context | None = None):
    """Per-bar structural stop price (None if the spec has no stop_at)."""
    if not spec.stop_at:
        return None
    ctx = ctx or _Context(spec, frame)
    return ctx.operand(spec.stop_at).to_numpy(float)


def overlay_lines(spec: StrategySpec, frame: pd.DataFrame):
    """Price-scale indicators for the chart (oscillators are not drawn on price)."""
    from app.strategies.base import Overlay

    lines = []
    for ind in spec.indicators:
        if ind.of is None and ind.type in ("sma", "ema", "highest", "lowest") and ind.source != "volume":
            lines.append(Overlay(f"{ind.id} ({ind.type.upper()} {ind.period})",
                                 _indicator_series(ind, frame)[None]))
        elif ind.type == "bb":
            s = _indicator_series(ind, frame)
            lines += [Overlay(f"{ind.id}.upper", s["upper"]), Overlay(f"{ind.id}.middle", s["middle"]),
                      Overlay(f"{ind.id}.lower", s["lower"])]
    return lines


# ------------------------------------------------------------ human readable
_OP_TR = {">": "büyüktür", "<": "küçüktür", ">=": "büyük veya eşit", "<=": "küçük veya eşit",
          "crosses_above": "yukarı keser", "crosses_below": "aşağı keser"}
_IND_TR = {"sma": "SMA", "ema": "EMA", "rsi": "RSI", "macd": "MACD", "bb": "Bollinger",
           "atr": "ATR", "highest": "En yüksek", "lowest": "En düşük", "roc": "Değişim %",
           "normalize": "Normalize (0–1)"}
_FIELD_TR = {"open": "açılış", "high": "yüksek", "low": "düşük", "close": "kapanış",
             "volume": "hacim", "hour": "saat (UTC)", "weekday": "haftanın günü (0=Pzt)",
             "body": "gövde", "upper_wick": "üst fitil", "lower_wick": "alt fitil",
             "range": "mum boyu"}


def _operand_tr(text: str) -> str:
    if NUMBER_RE.match(text):
        return text
    m = OPERAND_RE.match(text)
    name, shift = m.group("name"), int(m.group("shift") or 0)
    label = _FIELD_TR.get(name, name)
    return f"{label} ({shift} bar önce)" if shift else label


def describe(spec: StrategySpec) -> dict:
    def ind_tr(i: Indicator) -> str:
        if i.type == "macd":
            return f"{i.id} = MACD({i.fast}, {i.slow}, {i.signal})"
        if i.type == "bb":
            return f"{i.id} = Bollinger({i.period}, {i.std:g}σ) — {_FIELD_TR[i.source]}"
        if i.type == "atr":
            return f"{i.id} = ATR({i.period})"
        target = i.of if i.of else _FIELD_TR[i.source]
        return f"{i.id} = {_IND_TR[i.type]}({i.period}) — {target}"

    def cond_tr(c: Condition) -> str:
        right = _operand_tr(c.right)
        if c.right_mult != 1:
            right = f"{right} × {c.right_mult:g}"
        return f"{_operand_tr(c.left)} {_OP_TR[c.op]} {right}"

    def rules_tr(r: Rules) -> list[str]:
        lines = [cond_tr(c) for c in r.all]
        if r.any:
            lines.append("şunlardan en az biri: " + " | ".join(cond_tr(c) for c in r.any))
        return lines

    risk = []
    if spec.stop_loss_pct:
        risk.append(f"Stop: giriş fiyatının %{spec.stop_loss_pct:g} altında kapanış")
    if spec.stop_at:
        risk.append(f"Stop: sinyal mumundaki '{_operand_tr(spec.stop_at)}' seviyesi")
    if spec.take_profit_pct:
        risk.append(f"Hedef: giriş fiyatının %{spec.take_profit_pct:g} üstünde kapanış")
    if spec.take_profit_rr:
        risk.append(f"Hedef: riskin {spec.take_profit_rr:g} katı (1:{spec.take_profit_rr:g} R:R)")
    if spec.max_bars_in_trade:
        risk.append(f"En fazla {spec.max_bars_in_trade} bar pozisyonda kal")
    return {"indicators": [ind_tr(i) for i in spec.indicators], "entry": rules_tr(spec.entry),
            "exit": rules_tr(spec.exit), "risk": risk}
