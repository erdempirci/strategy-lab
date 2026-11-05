import os

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.data_adapters.binance import BinanceDataError, fetch_binance_ohlcv
from app.data_adapters.normalize import quality_report
from app.schemas import BacktestRequest
from app.services.backtester import run_ema_backtest
from app.services.market_data import demo_ohlcv

ENGINE_VERSION = "0.2.0"

app = FastAPI(
    title="Strategy Lab API",
    version=ENGINE_VERSION,
    description="Research-only strategy backtesting API. Live trading is disabled.",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    redoc_url=None,
)

# Every endpoint is served under /api (how the single-project Vercel deployment
# routes to this service, which receives the original path) and, for local use
# and backwards compatibility, at the bare path too.
router = APIRouter()

# The dashboard normally calls the API through its own server-side proxy, so
# CORS is only needed for direct browser access (e.g. local dev on :3000).
_origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",")]
app.add_middleware(CORSMiddleware, allow_origins=[o for o in _origins if o],
                   allow_methods=["GET", "POST"], allow_headers=["Content-Type"])


def _load_frame(source: str, symbol: str, timeframe: str, bars: int):
    try:
        if source == "binance":
            return fetch_binance_ohlcv(symbol, timeframe, bars), "binance_spot"
        return demo_ohlcv(symbol=symbol, timeframe=timeframe, bars=bars), "deterministic_demo"
    except BinanceDataError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _candles_with_ema(frame, fast_period: int, slow_period: int) -> list[dict]:
    data = frame.sort_values("timestamp").reset_index(drop=True)
    ema_fast = data["close"].ewm(span=fast_period, adjust=False).mean()
    ema_slow = data["close"].ewm(span=slow_period, adjust=False).mean()
    warm = slow_period - 1
    return [
        {
            "time": int(row.timestamp.timestamp()),
            "open": round(float(row.open), 8),
            "high": round(float(row.high), 8),
            "low": round(float(row.low), 8),
            "close": round(float(row.close), 8),
            "ema_fast": round(float(ema_fast.iloc[i]), 8) if i >= warm else None,
            "ema_slow": round(float(ema_slow.iloc[i]), 8) if i >= warm else None,
        }
        for i, row in enumerate(data.itertuples(index=False))
    ]


@router.get("/")
def root() -> dict[str, str]:
    return {
        "service": "Strategy Lab",
        "status": "running",
        "message": "API is online. Open /api/docs to use the API.",
        "docs": "/api/docs",
        "health": "/api/health",
    }


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "strategy-lab", "engine_version": ENGINE_VERSION}


@router.get("/market-data/demo")
def get_demo_data(symbol: str = "BTC/USDT", timeframe: str = "1h", bars: int = 500) -> dict:
    if bars < 50 or bars > 5000:
        raise HTTPException(status_code=400, detail="bars must be between 50 and 5000")
    frame, source = _load_frame("demo", symbol, timeframe, bars)
    return {"symbol": symbol, "timeframe": timeframe, "source": source, "bars": len(frame),
            "data": frame.tail(10).to_dict(orient="records")}


@router.get("/market-data/binance")
def get_binance_data(symbol: str = "BTC/USDT", timeframe: str = "1h", bars: int = 500) -> dict:
    if bars < 1 or bars > 5000:
        raise HTTPException(status_code=400, detail="bars must be between 1 and 5000")
    frame, source = _load_frame("binance", symbol, timeframe, bars)
    return {"symbol": symbol, "timeframe": timeframe, "source": source,
            "quality": quality_report(frame, timeframe),
            "data": frame.tail(10).to_dict(orient="records")}


@router.post("/backtests")
def create_backtest(request: BacktestRequest) -> dict:
    if request.strategy != "ema_crossover":
        raise HTTPException(status_code=400, detail="Only ema_crossover is available in MVP")

    frame, source = _load_frame(request.data_source, request.symbol, request.timeframe,
                                request.bars)
    try:
        result = run_ema_backtest(
            frame=frame,
            fast_period=request.fast_period,
            slow_period=request.slow_period,
            initial_capital=request.initial_capital,
            fee_bps=request.fee_bps,
            slippage_bps=request.slippage_bps,
            timeframe=request.timeframe,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not request.include_trades:
        result.pop("trades")
    if request.include_candles:
        result["candles"] = _candles_with_ema(frame, request.fast_period, request.slow_period)

    return {
        "symbol": request.symbol,
        "timeframe": request.timeframe,
        "strategy": request.strategy,
        "parameters": {"fast_period": request.fast_period, "slow_period": request.slow_period},
        "data_source": source,
        "synthetic_data": source == "deterministic_demo",
        "data_quality": quality_report(frame, request.timeframe),
        "engine_version": ENGINE_VERSION,
        "assumptions": {
            "signal": "evaluated at bar close",
            "execution": "next_bar_open",
            "position": "long_or_flat, 100% equity, no leverage",
            "fees": "fee_bps of notional on entry and exit",
            "slippage": "slippage_bps adverse to fill price",
            "open_position_at_end": "closed at last close, flagged forced_exit",
            "sharpe_annualization": "24/7 market calendar",
            "live_trading": False,
        },
        **result,
    }


app.include_router(router, prefix="/api")
app.include_router(router, include_in_schema=False)
