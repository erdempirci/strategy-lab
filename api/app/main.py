import hmac
import os

import pandas as pd
from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError

from app.ai.translator import AIError, active_provider, provider_label, translate
from app.data_adapters.binance import BinanceDataError
from app.data_adapters.csv_upload import (
    CSVDataError,
    looks_like_session_market,
    parse_ohlcv_csv,
)
from app.data_adapters.markets import MARKETS, load_ohlcv, market_of
from app.data_adapters.normalize import quality_report
from app.data_adapters.stocks import StockDataError
from app.data_adapters.yahoo import YahooDataError
from app.desk.api import router as desk_router
from app.discovery.candidates import FAMILY_LABELS
from app.discovery.engine import run_discovery
from app.schemas import (
    AIStrategyRequest,
    BacktestRequest,
    CompareRequest,
    CSVInspectRequest,
    CustomBacktestRequest,
    DiscoverRequest,
    SpecRequest,
)
from app.services.backtester import run_backtest
from app.services.risk_engine import RiskConfig, run_risk_backtest
from app.strategies import STRATEGIES, build_signal
from app.strategies.dsl import (
    StrategySpec,
    custom_signal,
    describe,
    overlay_lines,
    stop_levels,
    strategy_events,
)

ENGINE_VERSION = "0.7.0"

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


def _load_frame(source: str, symbol: str, timeframe: str, bars: int, csv_data: str | None = None):
    try:
        if source == "csv":
            if not csv_data:
                raise ValueError("CSV verisi yok: önce bir dosya yükle")
            frame, inferred = parse_ohlcv_csv(csv_data)
            if inferred != timeframe:
                raise ValueError(f"CSV'deki barlar {inferred}, ama {timeframe} seçili")
            return frame.tail(bars).reset_index(drop=True), "csv_upload"
        frame, label = load_ohlcv(source, symbol, timeframe, bars)
        return frame.sort_values("timestamp").reset_index(drop=True), label
    except (BinanceDataError, YahooDataError, StockDataError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


CHART_BARS = 5000      # chart shows the most recent bars; metrics always use all bars


def _clip(overlays: list[dict], candles: list[dict]) -> list[dict]:
    start = candles[0]["time"] if candles else 0
    return [{**o, "data": [p for p in o["data"] if p["time"] >= start]} for o in overlays]


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
    return params, _execute(frame, signal, req)


def _year_minutes(req, frame=None) -> int:
    """Calendar for annualising Sharpe: ~252 sessions (Yahoo futures / stocks, or an uploaded
    CSV with no weekend bars), 365 days for 24/7 crypto."""
    session = req.data_source == "yahoo" or (
        req.data_source == "csv" and frame is not None and looks_like_session_market(frame))
    return 252 * 24 * 60 if session else 365 * 24 * 60


def _quality(frame, req) -> dict:
    gap = _expected_gap(req.symbol)
    if gap is None and req.data_source == "yahoo":
        gap = pd.Timedelta(days=4)          # stock/ETF daily: weekends + holidays
    if req.data_source == "csv" and looks_like_session_market(frame):
        gap = pd.Timedelta(hours=80)        # FX / metals / indices: weekend closure
    return quality_report(frame, req.timeframe, gap)


def _execute(frame, signal, req, stop_pct=None, take_profit_pct=None, levels=None,
             take_profit_rr=None, events=None) -> dict:
    """Simple engine, or the risk engine when the request carries risk settings."""
    if req.risk is None:
        return run_backtest(frame, signal, req.timeframe, req.initial_capital,
                            req.fee_bps, req.slippage_bps, _year_minutes(req, frame))
    return run_risk_backtest(frame, signal, RiskConfig(**req.risk.model_dump()), req.timeframe,
                             req.initial_capital, req.fee_bps, req.slippage_bps,
                             stop_pct_override=stop_pct, take_profit_pct_override=take_profit_pct,
                             trading_minutes_per_year=_year_minutes(req, frame),
                             stop_levels=levels, take_profit_rr_override=take_profit_rr,
                             events=events)


def _risk_assumptions(req) -> dict:
    if req.risk is None:
        return {}
    return {"position": "risk-based size, capped at 1x equity; shorts assume a "
                        "short-capable account (futures / CFD / margin)",
            "stops": "intrabar; stop assumed before target in the same bar; gaps fill at open",
            "entries": "fresh 0->1 signal required (no re-entry after a stop)",
            "daily_loss_limit": "vs. UTC day start equity, incl. open P&L at bar low",
            "max_total_loss": "static, vs. initial capital"}


ASSUMPTIONS = {
    "signal": "evaluated at bar close",
    "execution": "next_bar_open",
    "position": "long / short / flat, 100% equity, no leverage",
    "fees": "fee_bps of notional on entry and exit",
    "slippage": "slippage_bps adverse to fill price",
    "open_position_at_end": "closed at last close, flagged forced_exit",
    "sharpe_annualization": "365 days (crypto) / 252 sessions (stocks)",
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


def _expected_gap(symbol: str):
    m = market_of(symbol)
    return m.expected_gap if m else None


@router.post("/csv/inspect")
def inspect_csv(request: CSVInspectRequest) -> dict:
    """Parse an uploaded CSV and report what the backtester will see."""
    try:
        frame, timeframe = parse_ohlcv_csv(request.csv_data)
    except CSVDataError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    session = looks_like_session_market(frame)
    gap = pd.Timedelta(hours=80) if session else None
    return {"bars": len(frame), "timeframe": timeframe, "session_market": session,
            "start": frame["timestamp"].iloc[0].isoformat(),
            "end": frame["timestamp"].iloc[-1].isoformat(),
            "last_close": float(frame["close"].iloc[-1]),
            "quality": quality_report(frame, timeframe, gap)}


@router.get("/markets")
def markets() -> dict:
    return {"markets": [{"symbol": m.symbol, "label": m.label, "fee_bps": m.fee_bps,
                         "slippage_bps": m.slippage_bps, "timeframes": ["1h", "2h", "4h", "1d"]}
                        for m in MARKETS.values()],
            "families": [{"id": k, "label": v} for k, v in FAMILY_LABELS.items()]}


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
                                request.bars, request.csv_data)
    try:
        params, result = _run(request.strategy, request.strategy_params(), frame, request)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not request.include_trades:
        result.pop("trades")
    spec = STRATEGIES[request.strategy]
    if request.include_candles:
        result["candles"] = _candles(frame.tail(CHART_BARS))
        result["overlays"] = _clip(_overlays(spec, frame, params), result["candles"])

    return {
        "symbol": request.symbol,
        "timeframe": request.timeframe,
        "strategy": request.strategy,
        "strategy_name": spec.name,
        "parameters": params,
        "data_source": source,
        "synthetic_data": source == "deterministic_demo",
        "data_quality": _quality(frame, request),
        "engine_version": ENGINE_VERSION,
        "assumptions": {**ASSUMPTIONS, **_risk_assumptions(request)},
        **result,
    }


COMPARE_METRICS = ("net_return_pct", "max_drawdown_pct", "trades", "win_rate_pct",
                   "profit_factor", "sharpe_annualized", "exposure_pct", "recovery_factor",
                   "total_fees")
COMPARE_RISK = ("worst_day_pct", "daily_limit_breaches", "account_failed",
                "avg_monthly_return_pct", "positive_months_pct")


@router.post("/compare")
def compare_strategies(request: CompareRequest) -> dict:
    """Run several strategies with default parameters on the SAME data."""
    ids = request.strategies or list(STRATEGIES)
    unknown = [s for s in ids if s not in STRATEGIES]
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown strategies: {unknown}")
    frame, source = _load_frame(request.data_source, request.symbol, request.timeframe,
                                request.bars, request.csv_data)
    rows = []
    for strategy_id in ids:
        row = {"strategy": strategy_id, "name": STRATEGIES[strategy_id].name}
        try:
            params, result = _run(strategy_id, {}, frame, request)
            row["parameters"] = params
            row["metrics"] = {k: result["metrics"][k] for k in COMPARE_METRICS}
            if "risk" in result:
                row["risk"] = {k: result["risk"][k] for k in COMPARE_RISK}
        except ValueError as exc:
            row["error"] = str(exc)
        rows.append(row)
    return {
        "symbol": request.symbol,
        "timeframe": request.timeframe,
        "data_source": source,
        "synthetic_data": source == "deterministic_demo",
        "data_quality": _quality(frame, request),
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
                                request.bars, request.csv_data)
    needed = max(100, 3 * spec.warmup())
    if len(frame) < needed:
        raise HTTPException(status_code=400, detail=f"Bu strateji en az {needed} bar istiyor")
    try:
        if request.risk is None:
            result = _execute(frame, custom_signal(spec, frame), request)
        else:
            sig = custom_signal(spec, frame, apply_stops=False)
            events = strategy_events(spec, frame)
            result = _execute(frame, sig, request,
                              stop_pct=spec.stop_loss_pct, take_profit_pct=spec.take_profit_pct,
                              levels=stop_levels(spec, frame, pd.Series(events["entry"])),
                              take_profit_rr=spec.take_profit_rr, events=events)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not request.include_trades:
        result.pop("trades")
    if request.include_candles:
        result["candles"] = _candles(frame.tail(CHART_BARS))
        result["overlays"] = _clip(_overlay_json(overlay_lines(spec, frame), frame),
                                   result["candles"])
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
        "data_quality": _quality(frame, request),
        "engine_version": ENGINE_VERSION,
        "assumptions": {**ASSUMPTIONS, **_risk_assumptions(request),
                        "stops": "intrabar (risk engine)" if request.risk
                        else "close-based, vs. actual fill price"},
        **result,
    }


@router.post("/discover")
def discover(request: DiscoverRequest) -> dict:
    """Search for strategies that pass train → validation → untouched holdout."""
    from app.schemas import RiskSettings

    frame, source = _load_frame(request.data_source, request.symbol, request.timeframe,
                                request.bars, request.csv_data)
    risk = request.risk or RiskSettings()
    try:
        out = run_discovery(frame, request.timeframe, RiskConfig(**risk.model_dump()),
                            request.initial_capital, request.fee_bps, request.slippage_bps,
                            n_candidates=request.n_candidates, seed=request.seed,
                            families=request.families)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"symbol": request.symbol, "timeframe": request.timeframe, "data_source": source,
            "synthetic_data": source == "deterministic_demo",
            "data_quality": _quality(frame, request),
            "risk": risk.model_dump(), "engine_version": ENGINE_VERSION, **out}


def _access_code() -> str | None:
    return os.getenv("AI_ACCESS_CODE") or None


@router.get("/ai/status")
def ai_status() -> dict:
    provider = active_provider()
    return {"enabled": provider is not None, "provider": provider,
            "provider_label": provider_label(provider),
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


router.include_router(desk_router)
app.include_router(router, prefix="/api")
app.include_router(router, include_in_schema=False)
