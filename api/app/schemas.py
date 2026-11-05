from typing import Any, Literal

from pydantic import BaseModel, Field


class MarketRequest(BaseModel):
    symbol: str = Field(default="BTC/USDT", min_length=3, max_length=30)
    timeframe: str = Field(default="1h", min_length=2, max_length=5)
    data_source: Literal["demo", "binance"] = "demo"
    bars: int = Field(default=1000, ge=100, le=5000)
    initial_capital: float = Field(default=10_000, gt=0)
    fee_bps: float = Field(default=5, ge=0, le=500)
    slippage_bps: float = Field(default=2, ge=0, le=500)


class BacktestRequest(MarketRequest):
    strategy: str = "ema_crossover"
    params: dict[str, float] = Field(default_factory=dict)
    # Legacy EMA-crossover fields, kept for backwards compatibility.
    fast_period: int | None = Field(default=None, ge=2, le=200)
    slow_period: int | None = Field(default=None, ge=3, le=500)
    include_trades: bool = True
    include_candles: bool = False

    def strategy_params(self) -> dict[str, float]:
        params = dict(self.params)
        if self.strategy == "ema_crossover":
            if self.fast_period is not None:
                params.setdefault("fast_period", self.fast_period)
            if self.slow_period is not None:
                params.setdefault("slow_period", self.slow_period)
        return params


class CompareRequest(MarketRequest):
    strategies: list[str] | None = None


class CustomBacktestRequest(MarketRequest):
    spec: dict[str, Any]
    include_trades: bool = True
    include_candles: bool = False


class SpecRequest(BaseModel):
    spec: dict[str, Any]


class AIStrategyRequest(BaseModel):
    text: str = Field(min_length=3, max_length=3000)
    access_code: str | None = Field(default=None, max_length=200)
