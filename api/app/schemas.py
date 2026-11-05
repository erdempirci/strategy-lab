from typing import Literal

from pydantic import BaseModel, Field, model_validator


class BacktestRequest(BaseModel):
    symbol: str = Field(default="BTC/USDT", min_length=3, max_length=30)
    timeframe: str = Field(default="1h", min_length=2, max_length=5)
    strategy: str = "ema_crossover"
    data_source: Literal["demo", "binance"] = "demo"
    bars: int = Field(default=1000, ge=100, le=5000)
    fast_period: int = Field(default=12, ge=2, le=200)
    slow_period: int = Field(default=26, ge=3, le=500)
    initial_capital: float = Field(default=10_000, gt=0)
    fee_bps: float = Field(default=5, ge=0, le=500)
    slippage_bps: float = Field(default=2, ge=0, le=500)
    include_trades: bool = True
    include_candles: bool = False

    @model_validator(mode="after")
    def _check_periods(self) -> "BacktestRequest":
        if self.fast_period >= self.slow_period:
            raise ValueError("fast_period must be less than slow_period")
        if self.bars < self.slow_period * 3:
            raise ValueError("bars must be at least 3 x slow_period for a meaningful test")
        return self
