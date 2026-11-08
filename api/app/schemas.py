from typing import Any, Literal

from pydantic import BaseModel, Field


class RiskSettings(BaseModel):
    """Prop-firm style risk rules. When sent, the risk engine is used."""
    risk_per_trade_pct: float = Field(default=0.5, gt=0, le=10)
    daily_loss_limit_pct: float | None = Field(default=3.0, gt=0, le=50)
    max_total_loss_pct: float | None = Field(default=10.0, gt=0, le=90)
    stop_type: Literal["atr", "pct"] = "atr"
    stop_atr_period: int = Field(default=14, ge=2, le=200)
    stop_atr_mult: float = Field(default=2.0, gt=0, le=20)
    stop_pct: float = Field(default=2.0, gt=0, le=50)
    take_profit_rr: float | None = Field(default=2.0, gt=0, le=20)


class MarketRequest(BaseModel):
    symbol: str = Field(default="BTC/USDT", min_length=3, max_length=30)
    timeframe: str = Field(default="1h", min_length=2, max_length=5)
    data_source: Literal["demo", "binance", "yahoo"] = "demo"
    bars: int = Field(default=1000, ge=100, le=20_000)
    initial_capital: float = Field(default=10_000, gt=0)
    fee_bps: float = Field(default=5, ge=0, le=500)
    slippage_bps: float = Field(default=2, ge=0, le=500)
    risk: RiskSettings | None = None


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


class DiscoverRequest(MarketRequest):
    bars: int = Field(default=10_000, ge=2000, le=20_000)
    n_candidates: int = Field(default=150, ge=20, le=300)
    seed: int = Field(default=42, ge=0, le=1_000_000)
    families: list[str] | None = Field(default=None, max_length=12)
