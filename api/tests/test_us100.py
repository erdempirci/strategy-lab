import httpx
import numpy as np
import pandas as pd

from app.data_adapters.markets import load_ohlcv, market_of
from app.data_adapters.normalize import quality_report
from app.data_adapters.yahoo import fetch_yahoo_ohlcv
from app.desk.engine import run_desk, volume_ratio
from app.desk.models import DeskSettings
from app.desk.store import MemoryStore, iso
from app.discovery.candidates import generate
from app.strategies.dsl import custom_signal

NOW = pd.Timestamp("2026-11-06T15:20:00Z")


def futures_hours(start="2026-10-25T22:00Z", end="2026-11-06T15:00Z"):
    """Hourly bars of a CME-style session: closed Fri 21:00 → Sun 22:00 and 21–22 daily."""
    ts = pd.date_range(start, end, freq="1h", tz="UTC")
    keep = (ts.hour != 21) & ~((ts.weekday == 4) & (ts.hour >= 21)) & (ts.weekday != 5) \
        & ~((ts.weekday == 6) & (ts.hour < 22))
    return ts[keep]


def chart_body(ts):
    r = np.random.default_rng(1)
    close = 27000 + np.cumsum(r.normal(0, 20, len(ts)))
    opn = np.r_[close[0], close[:-1]]
    t = [int(x.timestamp()) for x in ts] + [int(NOW.timestamp()) - 37]   # live quote row
    o = list(opn) + [close[-1]]
    o[5] = None                                                          # Yahoo null bar
    return {"chart": {"error": None, "result": [{"timestamp": t, "indicators": {"quote": [{
        "open": o, "high": list(np.maximum(opn, close) + 5) + [close[-1]],
        "low": list(np.minimum(opn, close) - 5) + [close[-1]], "close": list(close) + [close[-1]],
        "volume": [1000] * len(ts) + [3]}]}}]}}


def client_for(body, seen):
    def handler(req):
        seen.append(req)
        return httpx.Response(200, json=body)
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_yahoo_parses_drops_unclosed_and_resamples():
    ts = futures_hours()
    seen = []
    f = fetch_yahoo_ohlcv("NQ=F", "1h", 5000, now=NOW, client=client_for(chart_body(ts), seen))
    assert seen[0].url.path == "/v8/finance/chart/NQ=F" and seen[0].url.params["interval"] == "60m"
    assert (f["timestamp"].dt.minute == 0).all()                    # live row dropped
    assert len(f) == len(ts) - 1 - 1                                # null bar and the 15:00 bar
    assert f["timestamp"].iloc[-1] == pd.Timestamp("2026-11-06T14:00Z")   # ends 15:00, +15m ≤ 15:20
    q = quality_report(f, "1h", market_of("US100").expected_gap)
    assert q["gaps"] == 0                                # weekends and daily breaks are not gaps
    assert quality_report(f, "1h")["gaps"] >= 5

    f4 = fetch_yahoo_ohlcv("NQ=F", "4h", 500, now=NOW, client=client_for(chart_body(ts), []))
    assert set(f4["timestamp"].dt.hour) <= {0, 4, 8, 12, 16, 20}
    assert f4["timestamp"].iloc[-1] == pd.Timestamp("2026-11-06T08:00Z")  # 12:00 bar not closed
    one = f[(f["timestamp"] >= "2026-11-05T08:00Z") & (f["timestamp"] < "2026-11-05T12:00Z")]
    bar = f4[f4["timestamp"] == pd.Timestamp("2026-11-05T08:00Z")].iloc[0]
    assert bar["high"] == one["high"].max() and bar["close"] == one["close"].iloc[-1]


def test_unsupported_timeframe_is_a_user_error():
    import pytest
    with pytest.raises(ValueError):
        fetch_yahoo_ohlcv("NQ=F", "15m", 100, now=NOW, client=client_for({}, []))


def test_market_routing():
    assert market_of("us100").ticker == "NQ=F" and market_of("NAS100").symbol == "US100"
    assert market_of("BTC/USDT") is None
    f, label = load_ohlcv("demo", "US100", "1h", 300)
    assert label == "deterministic_demo" and len(f) == 300


def test_engulfing_family_is_textbook():
    cands = generate(40, seed=3, families=["engulfing"])
    assert cands and {c.family for c in cands} == {"engulfing"}
    i = 30
    ts = pd.date_range("2026-01-01", periods=60, freq="1h", tz="UTC")
    close = np.full(60, 100.0)
    opn = np.full(60, 100.0)
    opn[i - 1], close[i - 1] = 101.0, 99.0      # red bar
    opn[i], close[i] = 98.9, 101.5              # green body swallowing it
    f = pd.DataFrame({"timestamp": ts, "open": opn, "close": close, "high": np.maximum(opn, close) + 0.1,
                      "low": np.minimum(opn, close) - 0.1, "volume": 1.0})
    plain = next(c for c in cands if c.spec.name.startswith("Yutan boğa mumu")
                 and len(c.spec.entry.all) == 3)
    sig = custom_signal(plain.spec, f, apply_stops=False).to_numpy()
    assert sig[i] == 1 and np.nansum(sig[:i]) == 0


def test_desk_on_session_market():
    ts = futures_hours("2026-06-01T22:00Z", "2026-11-06T13:00Z")
    r = np.random.default_rng(5)
    close = 27000 * np.exp(np.cumsum(r.normal(0, 0.002, len(ts))))
    opn = np.r_[close[0], close[:-1]]
    vol = np.where(ts.hour.isin([14, 15, 16, 17, 18, 19]), 30000.0, 4000.0)
    f = pd.DataFrame({"timestamp": ts, "open": opn, "high": np.maximum(opn, close) * 1.001,
                      "low": np.minimum(opn, close) * 0.999, "close": close, "volume": vol})
    f_us = f.copy()
    f_us.attrs["market"] = "US100"
    i = int(np.where(ts.hour == 14)[0][-1])
    assert abs(volume_ratio(f_us, i, 30, "1h") - 1.0) < 1e-9   # same-hour: US open is normal
    assert volume_ratio(f, i, 30, "1h") > 2                    # plain average would flag it

    spec = next(c for c in generate(60, seed=1, families=["engulfing"]) if len(c.spec.entry.all) == 3)
    st = MemoryStore()
    st.set_setting("settings", DeskSettings(data_source="demo", watchlist=[]).model_dump())
    st.strategies.append({"id": "u1", "name": "Engulf", "symbol": "US100", "timeframe": "1h",
                          "spec": spec.spec.model_dump(exclude_none=True), "stop_atr_mult": 2.0,
                          "take_profit_rr": 2.0, "verdict": "robust", "discovery": {},
                          "active": True, "activated_at": iso(ts[len(ts) - 400]),
                          "created_at": iso(ts[len(ts) - 400])})

    def load(symbol, timeframe, bars):
        return f[f["timestamp"] <= clock["now"]].tail(bars + 1).reset_index(drop=True)

    clock = {}
    for k in range(len(ts) - 399, len(ts)):
        clock["now"] = ts[k] + pd.Timedelta(minutes=17)     # cron at :17, 15m feed delay
        run_desk(st, load, now=clock["now"])
    assert st.decisions
    assert all(d["late_bars"] == 0 for d in st.decisions)      # weekends are not "missed"
    assert not any("eksik bar" in r["text"] for d in st.decisions for r in d["roles"])
    assert any(d["verdict"] == "AL" for d in st.decisions)
