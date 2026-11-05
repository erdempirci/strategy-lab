"""Strategy definitions: declarative parameters + a pure signal function.

A strategy turns OHLCV into a 0/1 target-position series evaluated at each bar
CLOSE using only data up to that bar. NaN means "no decision yet" (warm-up).
The engine executes changes at the NEXT bar's open, so strategies never need to
shift anything themselves.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import pandas as pd


@dataclass(frozen=True)
class Param:
    name: str
    label: str
    default: float
    min: float
    max: float
    step: float = 1
    integer: bool = True

    def coerce(self, value: float) -> float | int:
        value = float(value)
        if not (self.min <= value <= self.max):
            raise ValueError(f"{self.name} must be between {self.min:g} and {self.max:g}")
        return round(value) if self.integer else value

    def to_dict(self) -> dict:
        return {"name": self.name, "label": self.label, "default": self.default, "min": self.min,
                "max": self.max, "step": self.step, "integer": self.integer}


@dataclass(frozen=True)
class Overlay:
    name: str
    values: pd.Series
    color: str | None = None


SignalFn = Callable[..., pd.Series]
OverlayFn = Callable[..., list[Overlay]]
CheckFn = Callable[[dict, str], None]


@dataclass(frozen=True)
class Strategy:
    id: str
    name: str
    description: str
    params: tuple[Param, ...]
    signal: SignalFn
    warmup: Callable[[dict], int]
    overlays: OverlayFn = field(default=lambda frame, **p: [])
    check: CheckFn | None = None
    category: str = "trend"

    def resolve(self, raw: dict | None, timeframe: str = "1h") -> dict:
        raw = dict(raw or {})
        unknown = set(raw) - {p.name for p in self.params}
        if unknown:
            raise ValueError(f"Unknown parameter(s) for {self.id}: {sorted(unknown)}")
        resolved = {p.name: p.coerce(raw.get(p.name, p.default)) for p in self.params}
        if self.check:
            self.check(resolved, timeframe)
        return resolved

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "description": self.description,
                "category": self.category, "params": [p.to_dict() for p in self.params]}
