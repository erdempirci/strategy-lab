"""HTTP endpoints of the desk (mounted under /api/desk)."""

from __future__ import annotations

import hmac
import os

import pandas as pd
from fastapi import APIRouter, Header, HTTPException
from pydantic import ValidationError

from app.data_adapters.markets import load_ohlcv, market_of
from app.data_adapters.yahoo import SUPPORTED as YAHOO_TIMEFRAMES
from app.desk.engine import run_desk, settings_of
from app.desk.ledger import r_stats
from app.desk.models import RunIn, SettingsIn, StrategyIn, StrategyPatch
from app.desk.report import weekly_report
from app.desk.store import StoreError, get_store, iso
from app.strategies.dsl import StrategySpec, describe

router = APIRouter(prefix="/desk", tags=["desk"])


def _store():
    return get_store()


def _require_write(access_code: str | None, store) -> None:
    code = os.getenv("DESK_ACCESS_CODE")
    if code:
        if not hmac.compare_digest(code, access_code or ""):
            raise HTTPException(status_code=401, detail="Masa erişim kodu hatalı")
    elif store.persistent:
        raise HTTPException(status_code=403, detail="DESK_ACCESS_CODE ayarlanmadan masa "
                            "değiştirilemez (Vercel ortam değişkeni).")


def _authorized_run(authorization: str | None, access_code: str | None, store) -> str:
    secret = os.getenv("CRON_SECRET")
    if secret and authorization and hmac.compare_digest(authorization, f"Bearer {secret}"):
        return "schedule"
    _require_write(access_code, store)
    return "manual"


def loader_for(source: str):
    """Crypto pairs → Binance, US100/US500 → Yahoo futures; "demo" → synthetic for all."""
    def load(symbol: str, timeframe: str, bars: int) -> pd.DataFrame:
        return load_ohlcv(source, symbol, timeframe, bars)[0]
    return load


def _guard(fn):
    try:
        return fn()
    except StoreError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/status")
def status() -> dict:
    store = _store()
    return {"storage": store.name, "persistent": store.persistent,
            "access_code_required": bool(os.getenv("DESK_ACCESS_CODE")) or store.persistent,
            "access_code_set": bool(os.getenv("DESK_ACCESS_CODE")),
            "schedule_secret_set": bool(os.getenv("CRON_SECRET")),
            "orders": "disabled — paper only"}


@router.get("/state")
def state() -> dict:
    store = _store()

    def build():
        strategies = store.list_strategies()
        shadow = [t for t in store.list_trades(kind="shadow") if t["exit_time"]]
        desk = [t for t in store.list_trades(kind="desk") if t["exit_time"]]
        for s in strategies:
            s["rules"] = describe(StrategySpec.model_validate(s["spec"]))
            s["forward"] = r_stats([t for t in shadow if t["strategy_id"] == s["id"]])
            s["desk_stats"] = r_stats([t for t in desk if t["strategy_id"] == s["id"]])
        return {"status": status(), "settings": settings_of(store).model_dump(),
                "strategies": strategies, "snapshot": store.get_setting("snapshot"),
                "decisions": store.list_decisions(limit=60),
                "trades": sorted(desk, key=lambda t: t["exit_time"], reverse=True)[:50],
                "volume_spikes": [e["payload"] for e in store.list_events("volume_spike", 20)],
                "runs": [e["payload"] | {"created_at": e["created_at"]}
                         for e in store.list_events("run", 12)]}
    return _guard(build)


@router.post("/strategies")
def add_strategy(body: StrategyIn) -> dict:
    store = _store()
    _require_write(body.access_code, store)
    if body.verdict == "failed_holdout":
        raise HTTPException(status_code=400, detail="Görmediği veride başarısız olan strateji "
                            "masaya alınmaz.")
    m = market_of(body.symbol)
    if m and body.timeframe not in YAHOO_TIMEFRAMES:
        raise HTTPException(status_code=400, detail=f"{m.symbol} için zaman dilimi "
                            f"{', '.join(YAHOO_TIMEFRAMES)} olmalı.")
    try:
        spec = StrategySpec.model_validate(body.spec)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=f"Geçersiz strateji: {exc.errors()[0]['msg']}") from exc
    existing = [s for s in _guard(lambda: store.list_strategies(active=True))
                if s["symbol"] == body.symbol and s["timeframe"] == body.timeframe
                and s["spec"] == spec.model_dump(exclude_none=True)]
    if existing:
        raise HTTPException(status_code=409, detail="Bu strateji zaten masada.")
    row = body.model_dump(exclude={"access_code", "spec"})
    row["spec"] = spec.model_dump(exclude_none=True)
    row["activated_at"] = iso(pd.Timestamp.now(tz="UTC"))
    return _guard(lambda: store.add_strategy(row))


@router.patch("/strategies/{strategy_id}")
def patch_strategy(strategy_id: str, body: StrategyPatch) -> dict:
    store = _store()
    _require_write(body.access_code, store)
    patch = {"active": body.active}
    if body.active:   # a reactivated strategy starts a fresh forward test window
        patch["activated_at"] = iso(pd.Timestamp.now(tz="UTC"))
    out = _guard(lambda: store.update_strategy(strategy_id, patch))
    if out is None:
        raise HTTPException(status_code=404, detail="Strateji bulunamadı")
    return out


@router.put("/settings")
def put_settings(body: SettingsIn) -> dict:
    store = _store()
    _require_write(body.access_code, store)
    value = body.model_dump(exclude={"access_code"})
    _guard(lambda: store.set_setting("settings", value))
    return value


@router.post("/run")
def run(body: RunIn | None = None, authorization: str | None = Header(default=None)) -> dict:
    store = _store()
    trigger = _authorized_run(authorization, body.access_code if body else None, store)
    settings = settings_of(store)
    return _guard(lambda: run_desk(store, loader_for(settings.data_source), trigger=trigger))


@router.get("/report")
def report(days: int = 7) -> dict:
    if days < 1 or days > 90:
        raise HTTPException(status_code=400, detail="days 1–90 arası olmalı")
    return _guard(lambda: weekly_report(_store(), days))
