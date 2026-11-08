import json

import httpx
import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.desk import store as store_mod
from app.desk.engine import Book, _keep_runs, evaluate, risk_check, run_desk
from app.desk.ledger import build_ledger
from app.desk.models import DeskSettings
from app.desk.report import weekly_report
from app.desk.store import MemoryStore, SupabaseStore, iso
from app.main import app
from app.services.risk_engine import RiskConfig, run_risk_backtest
from app.strategies.dsl import StrategySpec, custom_signal

SPEC = {"name": "EMA 9/21",
        "indicators": [{"id": "ef", "type": "ema", "period": 9},
                       {"id": "es", "type": "ema", "period": 21}],
        "entry": {"all": [{"left": "ef", "op": "crosses_above", "right": "es"}]},
        "exit": {"all": [{"left": "ef", "op": "crosses_below", "right": "es"}]}}


def market(n=1500, seed=3, phi=0.15):
    r = np.random.default_rng(seed)
    e = r.normal(0, 0.006, n)
    x = np.zeros(n)
    for i in range(1, n):
        x[i] = phi * x[i - 1] + e[i]
    close = 100 * np.exp(np.cumsum(x))
    open_ = np.r_[close[0], close[:-1]]
    sp = np.abs(r.normal(0.0015, 0.0007, n))
    return pd.DataFrame({"timestamp": pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC"),
                         "open": open_, "high": np.maximum(open_, close) * (1 + sp),
                         "low": np.minimum(open_, close) * (1 - sp), "close": close,
                         "volume": r.lognormal(5, 0.3, n)})


def exchange(frame):
    """Loader that, like an exchange, only knows bars that have started by `now`."""
    state = {"now": frame["timestamp"].iloc[0]}

    def load(symbol, timeframe, bars):
        f = frame[frame["timestamp"] <= state["now"]]
        return f.tail(bars + 1).reset_index(drop=True)
    return load, state


def strategy_row(frame, start=500, verdict="robust", **kw):
    return {"id": "s1", "name": "EMA 9/21", "symbol": "BTC/USDT", "timeframe": "1h",
            "spec": StrategySpec.model_validate(SPEC).model_dump(exclude_none=True),
            "stop_atr_mult": 2.0, "take_profit_rr": 2.0, "verdict": verdict,
            "discovery": {"holdout": {"trades": 30, "profit_factor": 1.3, "avg_r": 0.2}},
            "active": True, "activated_at": iso(frame["timestamp"].iloc[start]),
            "created_at": iso(frame["timestamp"].iloc[start]), **kw}


def desk_store(frame, **kw):
    st = MemoryStore()
    st.set_setting("settings", DeskSettings(data_source="demo", watchlist=[], **kw).model_dump())
    st.strategies.append(strategy_row(frame))
    return st


def test_keep_runs_zeroes_unapproved_runs():
    sig = np.array([np.nan, 0, 1, 1, 0, 1, 0, 1, 1])
    out = _keep_runs(sig, {5})
    assert np.isnan(out[0])
    assert list(out[1:]) == [0, 0, 0, 0, 1, 0, 0, 0]


def test_all_approved_desk_equals_engine():
    """Paper trades are the risk engine's trades — no second simulator to drift."""
    f = market()
    s = strategy_row(f, start=0)
    sig = custom_signal(StrategySpec.model_validate(SPEC), f, apply_stops=False).to_numpy(float)
    trans = {iso(f["timestamp"].iloc[i]) for i in range(1, len(sig))
             if sig[i] == 1 and sig[i - 1] == 0}
    ev = evaluate(s, f, DeskSettings(), trans)
    ref = run_risk_backtest(f, pd.Series(sig), RiskConfig(daily_loss_limit_pct=None,
                            max_total_loss_pct=None, take_profit_rr=2.0), "1h", 10_000, 5, 2)
    assert len(ev.desk) == len(ref["trades"]) > 20
    assert [t["r_multiple"] for t in ev.desk] == [t["r_multiple"] for t in ref["trades"]]
    assert [t["r_multiple"] for t in ev.shadow] == [t["r_multiple"] for t in ev.desk]
    none = evaluate(s, f, DeskSettings(), set())
    assert none.desk == [] and len(none.shadow) == len(ev.shadow)


def test_walk_forward_run_is_consistent_and_idempotent():
    f = market()
    st = desk_store(f)
    load, clock = exchange(f)
    for i in range(520, 900):
        clock["now"] = f["timestamp"].iloc[i]
        run_desk(st, load, now=clock["now"])
    n_dec, n_tr = len(st.decisions), len(st.trades)
    run_desk(st, load, now=clock["now"])                  # same bar again
    assert (len(st.decisions), len(st.trades)) == (n_dec, n_tr)

    sig = custom_signal(StrategySpec.model_validate(SPEC), f, apply_stops=False).to_numpy(float)
    expected = [i for i in range(500, 899) if sig[i] == 1 and sig[i - 1] == 0]
    assert n_dec == len(expected) >= 3                     # every signal bar decided exactly once
    approved = {d["signal_time"] for d in st.decisions if d["verdict"] == "AL"}
    assert approved, "a robust strategy with clean data should get AL decisions"
    assert all(d["late_bars"] == 0 for d in st.decisions)
    shadow = {t["signal_time"]: t for t in st.list_trades(kind="shadow")}
    for t in st.list_trades(kind="desk"):
        assert t["signal_time"] in approved
        step = pd.Timestamp(t["entry_time"]) - pd.Timestamp(t["signal_time"])
        assert step == pd.Timedelta(hours=1)                # entry = next bar open
        assert shadow[t["signal_time"]]["r_multiple"] == t["r_multiple"]
    for d in st.decisions:                                 # every role spoke
        roles = [r["role"] for r in d["roles"]]
        assert roles[0] == "Tarayıcı" and roles[-2:] == ["Şüpheci", "Günlükçü"]

    snap = st.get_setting("snapshot")
    json.dumps([st.decisions, st.trades, st.events, snap], allow_nan=False)   # plain JSON only
    closed = [t for t in st.list_trades(kind="desk") if t["exit_time"]]
    led = build_ledger(closed, [], 10_000, 0.5, clock["now"])
    assert snap["account"]["realized_equity"] == led["realized_equity"]
    rep = weekly_report(st, days=30, now=clock["now"])
    assert rep["decisions"]["total"] > 0 and isinstance(rep["suggestions"], list)


def test_missed_signals_are_journaled_not_entered():
    f = market()
    st = desk_store(f)
    load, clock = exchange(f)
    clock["now"] = f["timestamp"].iloc[520]
    run_desk(st, load, now=clock["now"])
    clock["now"] = f["timestamp"].iloc[700]                # desk was off for a week
    run_desk(st, load, now=clock["now"])
    late = [d for d in st.decisions if d["late_bars"] > 0]
    assert late and all(d["verdict"] == "GEÇ" for d in late)
    assert all(t["signal_time"] not in {d["signal_time"] for d in late}
               for t in st.list_trades(kind="desk"))


def test_promising_strategy_waits_until_proven():
    f = market()
    st = MemoryStore()
    st.set_setting("settings", DeskSettings(data_source="demo", watchlist=[]).model_dump())
    st.strategies.append(strategy_row(f, verdict="promising"))
    load, clock = exchange(f)
    for i in range(520, 700):
        clock["now"] = f["timestamp"].iloc[i]
        run_desk(st, load, now=clock["now"])
    assert st.decisions and all(d["verdict"] != "AL" for d in st.decisions)
    assert not st.list_trades(kind="desk")
    assert st.list_trades(kind="shadow"), "blocked signals are still tracked in the shadow book"


def book(**kw):
    base = {"settings": DeskSettings(), "realized": 10_000, "equity": 10_000,
            "day_start": 10_000, "open_risk": 0.0, "open_count": 0, "open_symbols": set()}
    return Book(**{**base, **kw})


PLAN = {"entry_ref": 100.0, "stop": 98.0, "target": 104.0, "stop_dist_pct": 2.0, "rr": 2.0}


def test_risk_sizes_and_blocks():
    ok = risk_check(book(), PLAN)
    assert ok["status"] == "OK" and ok["risk_amount"] == 50.0
    assert ok["quantity"] == pytest.approx(25.0)           # 50 / (100 - 98)
    assert risk_check(book(equity=9_690, realized=9_690), PLAN)["status"] == "RED"   # -3.1% today
    assert risk_check(book(open_count=3), PLAN)["status"] == "RED"
    # all open stops + this one would cross the daily floor (9,700)
    assert risk_check(book(realized=9_800, equity=9_800, open_risk=80), PLAN)["status"] == "RED"
    assert risk_check(book(realized=9_000, equity=9_000, day_start=9_000), PLAN)["status"] == "RED"


def test_ledger_compounds_on_desk_equity():
    closed = [{"strategy_id": "a", "entry_time": "2026-01-01T00:00:00+00:00",
               "exit_time": "2026-01-01T05:00:00+00:00", "return_pct": 1.0},
              {"strategy_id": "a", "entry_time": "2026-01-01T06:00:00+00:00",
               "exit_time": "2026-01-02T01:00:00+00:00", "return_pct": -0.5}]
    led = build_ledger(closed, [], 10_000, 0.5, pd.Timestamp("2026-01-02T12:00", tz="UTC"))
    assert led["closed"][1]["equity_at_entry"] == 10_100
    assert led["realized_equity"] == pytest.approx(10_100 * 0.995)
    assert led["day_start_equity"] == 10_100


def test_store_dedupes():
    st = MemoryStore()
    row = {"strategy_id": "a", "signal_time": "2026-01-01T00:00:00+00:00", "verdict": "AL"}
    assert st.add_decision(row) and not st.add_decision(dict(row))
    t = {"strategy_id": "a", "kind": "desk", "entry_time": "2026-01-01T01:00:00+00:00"}
    assert st.add_trades([t, dict(t)]) == 1
    assert st.add_event({"kind": "x", "dedupe_key": "k"}) and not st.add_event({"kind": "x", "dedupe_key": "k"})


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("DESK_ACCESS_CODE", raising=False)
    monkeypatch.setattr(store_mod, "_memory", None)
    return TestClient(app)


def test_api_flow(client, monkeypatch):
    assert client.get("/api/desk/status").json()["storage"] == "memory"
    body = {"name": "EMA 9/21", "symbol": "BTC/USDT", "timeframe": "1h", "spec": SPEC,
            "stop_atr_mult": 2, "take_profit_rr": 2, "verdict": "failed_holdout"}
    assert client.post("/api/desk/strategies", json=body).status_code == 400
    assert client.post("/api/desk/strategies", json={**body, "timeframe": "15m",
                                                     "verdict": "robust"}).status_code == 422
    r = client.post("/api/desk/strategies", json={**body, "verdict": "robust"})
    assert r.status_code == 200
    assert client.post("/api/desk/strategies", json={**body, "verdict": "robust"}).status_code == 409
    assert client.put("/api/desk/settings", json={"data_source": "demo",
                                                  "watchlist": ["BTC/USDT"]}).status_code == 200
    out = client.post("/api/desk/run", json={}).json()
    assert out["strategies"] == 1 and "snapshot" in out
    s = client.get("/api/desk/state").json()
    assert s["strategies"][0]["rules"] and s["snapshot"]["account"]["equity"] > 0
    assert client.get("/api/desk/report").status_code == 200
    sid = s["strategies"][0]["id"]
    assert client.patch(f"/api/desk/strategies/{sid}", json={"active": False}).json()["active"] is False

    monkeypatch.setenv("DESK_ACCESS_CODE", "kod")
    monkeypatch.setenv("CRON_SECRET", "gizli")
    assert client.post("/api/desk/run", json={}).status_code == 401
    assert client.post("/api/desk/run", json={"access_code": "kod"}).status_code == 200
    r = client.post("/api/desk/run", headers={"Authorization": "Bearer gizli"})
    assert r.status_code == 200
    assert client.get("/api/desk/state").json()["runs"][0]["trigger"] == "schedule"


def test_supabase_store_requests():
    seen = []

    def handler(req: httpx.Request):
        seen.append(req)
        if req.method == "GET":
            return httpx.Response(200, json=[{"value": {"a": 1}}])
        return httpx.Response(201, json=[{"ok": True}])

    client = httpx.Client(base_url="https://x.supabase.co/rest/v1",
                          transport=httpx.MockTransport(handler))
    st = SupabaseStore("https://x.supabase.co", "key", client=client)
    assert st.get_setting("settings") == {"a": 1}
    assert st.add_decision({"strategy_id": "a", "signal_time": "t", "v": np.float64(1.5),
                            "n": np.int64(2)})
    assert st.add_trades([{"strategy_id": "a", "kind": "desk", "entry_time": "t"}]) == 1
    post = seen[1]
    assert post.url.params["on_conflict"] == "strategy_id,signal_time"
    assert "ignore-duplicates" in post.headers["Prefer"]
    assert json.loads(post.content) == [{"strategy_id": "a", "signal_time": "t", "v": 1.5, "n": 2}]
    assert seen[0].url.params["key"] == "eq.settings"
