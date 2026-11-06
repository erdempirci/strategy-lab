"""Desk settings and request models."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

# Hourly runs can only act on bars of at least one hour: a 15m strategy would fire
# three signals between two runs and the desk would miss them.
DESK_TIMEFRAMES = ("1h", "2h", "4h", "6h", "8h", "12h", "1d")


class DeskSettings(BaseModel):
    initial_capital: float = Field(default=10_000, gt=0)
    risk_per_trade_pct: float = Field(default=0.5, gt=0, le=5)
    daily_loss_limit_pct: float = Field(default=3.0, gt=0, le=50)
    max_total_loss_pct: float = Field(default=10.0, gt=0, le=90)
    max_open_positions: int = Field(default=3, ge=1, le=20)
    fee_bps: float = Field(default=5, ge=0, le=500)
    slippage_bps: float = Field(default=2, ge=0, le=500)
    data_source: Literal["binance", "demo"] = "binance"
    watchlist: list[str] = Field(default_factory=lambda: ["BTC/USDT", "ETH/USDT", "SOL/USDT"],
                                 max_length=20)
    scan_timeframe: Literal["1h", "2h", "4h", "1d"] = "1h"
    volume_spike_x: float = Field(default=2.0, gt=1, le=20)
    volume_lookback_days: int = Field(default=30, ge=3, le=90)
    max_late_bars: int = Field(default=1, ge=1, le=3)


class StrategyIn(BaseModel):
    """A discovery finalist sent from the dashboard ("Masaya ekle")."""
    name: str = Field(min_length=1, max_length=120)
    symbol: str = Field(min_length=3, max_length=30)
    timeframe: str
    spec: dict[str, Any]
    stop_atr_mult: float = Field(gt=0, le=20)
    take_profit_rr: float | None = Field(default=None, gt=0, le=20)
    verdict: Literal["robust", "promising", "failed_holdout"]
    discovery: dict[str, Any] = Field(default_factory=dict)
    access_code: str | None = Field(default=None, max_length=200)

    @field_validator("timeframe")
    @classmethod
    def _tf(cls, v: str) -> str:
        if v not in DESK_TIMEFRAMES:
            raise ValueError(f"Masa saatlik çalışır; zaman dilimi şunlardan biri olmalı: "
                             f"{', '.join(DESK_TIMEFRAMES)}")
        return v


class StrategyPatch(BaseModel):
    active: bool
    access_code: str | None = Field(default=None, max_length=200)


class SettingsIn(DeskSettings):
    access_code: str | None = Field(default=None, max_length=200)


class RunIn(BaseModel):
    access_code: str | None = Field(default=None, max_length=200)
