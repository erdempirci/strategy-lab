import hmac
import os

import pandas as pd
from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError

from app.ai.translator import AIError, translate
from app.data_adapters.binance import BinanceDataError, fetch_binance_ohlcv
from app.data_adapters.normalize import quality_report
from app.schemas import (
    AIStrategyRequest,
    BacktestRequest,
    CompareRequest,
    CustomBacktestRequest,
    SpecRequest,
)
from app.services.backtester import run_backtest
from app.services.market_data import demo_ohlcv
from app.strategies import STRATEGIES, build_signal
from app.strategies.dsl import StrategySpec, custom_signal, describe, overlay_lines

ENGINE_VERSION = "0.4.0"

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
            frame, label = fetch_binance_ohlcv(symbol, timeframe, bars), "binance_spot"
        else:
            frame = demo_ohlcv(symbol=symbol, timeframe=timeframe, bars=bars)
            label = "deterministic_demo"
        return frame.sort_values("timestamp").reset_index(drop=True), label
    except BinanceDataError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _candles(frame) -> list[dict]:
    return [
        {"time": int(r.timestamp.timestamp()), "open": round(float(r.open), 8),
         "high": round(float(r.high), 8), "low": round(float(r.low), 8),
         "close": round(float(r.close), 8)}
        for r in frame.itertuples(index=False)
    ]


def _overlays(spec, frame, params) -> list[dict]:
    return _overlay_json(spec.overlays(frame, **params), frame)


def _overlay_json(overlays, frame) -> list[dict]:
    times = [int(t.timestamp()) for t in frame["timestamp"]]
    out = []
    for ov in overlays:
        values = ov.values.reset_index(drop=True)
        points = [{"time": t, "value": round(float(v), 8)}
                  for t, v in zip(times, values, strict=True) if pd.notna(v)]
        out.append({"name": ov.name, "color": ov.color, "data": points})
    return out


def _run(strategy_id: str, raw_params: dict, frame, req) -> tuple[dict, dict]:
    """Validate params and run one strategy. Raises ValueError on bad input."""
    spec = STRATEGIES.get(strategy_id)
    if spec is None:
        raise ValueError(f"Unknown strategy {strategy_id!r}. See GET /api/strategies")
    params = spec.resolve(raw_params, req.timeframe)
    needed = max(100, 3 * spec.warmup(params))
    if len(frame) < needed:
        raise ValueError(f"{spec.name} needs at least {needed} bars with these parameters")
    signal = build_signal(strategy_id, frame, params, req.timeframe)
    result = run_backtest(frame, signal, req.timeframe, req.initial_capital,
                          req.fee_bps, req.slippage_bps)
    return params, result


ASSUMPTIONS = {
    "signal": "evaluated at bar close",
    "execution": "next_bar_open",
    "position": "long_or_flat, 100% equity, no leverage",
    "fees": "fee_bps of notional on entry and exit",
    "slippage": "slippage_bps adverse to fill price",
    "open_position_at_end": "closed at last close, flagged forced_exit",
    "sharpe_annualization": "24/7 market calendar",
    "session_hours": "UTC",
    "live_trading": False,
}


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


@router.get("/strategies")
def list_strategies() -> dict:
    return {"strategies": [spec.to_dict() for spec in STRATEGIES.values()]}


@router.post("/backtests")
def create_backtest(request: BacktestRequest) -> dict:
    if request.strategy not in STRATEGIES:
        raise HTTPException(status_code=400, detail=f"Unknown strategy {request.strategy!r}")
    frame, source = _load_frame(request.data_source, request.symbol, request.timeframe,
                                request.bars)
    try:
        params, result = _run(request.strategy, request.strategy_params(), frame, request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not request.include_trades:
        result.pop("trades")
    spec = STRATEGIES[request.strategy]
    if request.include_candles:
        result["candles"] = _candles(frame)
        result["overlays"] = _overlays(spec, frame, params)

    return {
        "symbol": request.symbol,
        "timeframe": request.timeframe,
        "strategy": request.strategy,
        "strategy_name": spec.name,
        "parameters": params,
        "data_source": source,
        "synthetic_data": source == "deterministic_demo",
        "data_quality": quality_report(frame, request.timeframe),
        "engine_version": ENGINE_VERSION,
        "assumptions": ASSUMPTIONS,
        **result,
    }


COMPARE_METRICS = ("net_return_pct", "max_drawdown_pct", "trades", "win_rate_pct",
                   "profit_factor", "sharpe_annualized", "exposure_pct", "recovery_factor",
                   "total_fees")


@router.post("/compare")
def compare_strategies(request: CompareRequest) -> dict:
    """Run several strategies with default parameters on the SAME data."""
    ids = request.strategies or list(STRATEGIES)
    unknown = [s for s in ids if s not in STRATEGIES]
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown strategies: {unknown}")
    frame, source = _load_frame(request.data_source, request.symbol, request.timeframe,
                                request.bars)
    rows = []
    for strategy_id in ids:
        row = {"strategy": strategy_id, "name": STRATEGIES[strategy_id].name}
        try:
            params, result = _run(strategy_id, {}, frame, request)
            row["parameters"] = params
            row["metrics"] = {k: result["metrics"][k] for k in COMPARE_METRICS}
        except ValueError as exc:
            row["error"] = str(exc)
        rows.append(row)
    return {
        "symbol": request.symbol,
        "timeframe": request.timeframe,
        "data_source": source,
        "synthetic_data": source == "deterministic_demo",
        "data_quality": quality_report(frame, request.timeframe),
        "engine_version": ENGINE_VERSION,
        "note": "Default parameters, in-sample only. Not validated out-of-sample.",
        "results": rows,
    }


def _spec_or_400(raw: dict) -> StrategySpec:
    try:
        return StrategySpec.model_validate(raw)
    except ValidationError as exc:
        problems = []
        for err in exc.errors(include_url=False)[:6]:
            where = ".".join(str(p) for p in err["loc"])
            problems.append(f"{where}: {err['msg'].replace('Value error, ', '')}" if where
                            else err["msg"].replace("Value error, ", ""))
        raise HTTPException(status_code=400, detail="; ".join(problems)) from exc


@router.post("/custom/validate")
def validate_custom(request: SpecRequest) -> dict:
    spec = _spec_or_400(request.spec)
    return {"spec": spec.model_dump(exclude_none=True), "rules": describe(spec),
            "warmup_bars": spec.warmup()}


@router.post("/custom/backtest")
def backtest_custom(request: CustomBacktestRequest) -> dict:
    spec = _spec_or_400(request.spec)
    frame, source = _load_frame(request.data_source, request.symbol, request.timeframe,
                                request.bars)
    needed = max(100, 3 * spec.warmup())
    if len(frame) < needed:
        raise HTTPException(status_code=400, detail=f"Bu strateji en az {needed} bar istiyor")
    try:
        result = run_backtest(frame, custom_signal(spec, frame), request.timeframe,
                              request.initial_capital, request.fee_bps, request.slippage_bps)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not request.include_trades:
        result.pop("trades")
    if request.include_candles:
        result["candles"] = _candles(frame)
        result["overlays"] = _overlay_json(overlay_lines(spec, frame), frame)
    return {
        "symbol": request.symbol,
        "timeframe": request.timeframe,
        "strategy": "custom",
        "strategy_name": spec.name,
        "parameters": {},
        "spec": spec.model_dump(exclude_none=True),
        "rules": describe(spec),
        "data_source": source,
        "synthetic_data": source == "deterministic_demo",
        "data_quality": quality_report(frame, request.timeframe),
        "engine_version": ENGINE_VERSION,
        "assumptions": {**ASSUMPTIONS, "stops": "close-based, vs. actual fill price"},
        **result,
    }


def _access_code() -> str | None:
    return os.getenv("AI_ACCESS_CODE") or None


@router.get("/ai/status")
def ai_status() -> dict:
    return {"enabled": bool(os.getenv("ANTHROPIC_API_KEY")),
            "access_code_required": _access_code() is not None}


@router.post("/ai/strategy")
def ai_strategy(request: AIStrategyRequest) -> dict:
    code = _access_code()
    if code and not hmac.compare_digest(code, request.access_code or ""):
        raise HTTPException(status_code=401, detail="Erişim kodu hatalı")
    try:
        out = translate(request.text)
    except AIError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    spec = StrategySpec.model_validate(out["spec"])
    return {**out, "rules": describe(spec)}


app.include_router(router, prefix="/api")
app.include_router(router, include_in_schema=False)
