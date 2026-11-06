import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.discovery.candidates import generate
from app.discovery.engine import deflated_sharpe, effective_trials, run_discovery
from app.main import app
from app.services.market_data import demo_ohlcv
from app.services.risk_engine import RiskConfig


def ar1_market(n, phi, seed):
    """Synthetic market with momentum strength phi (0 = random walk, no edge)."""
    r = np.random.default_rng(seed)
    e = r.normal(0, 0.006, n)
    x = np.zeros(n)
    for i in range(1, n):
        x[i] = phi * x[i - 1] + e[i]
    close = 100 * np.exp(np.cumsum(x))
    open_ = np.r_[close[0], close[:-1]]
    sp = np.abs(r.normal(0.0015, 0.0007, n))
    return pd.DataFrame({"timestamp": pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC"),
                         "open": open_, "high": np.maximum(open_, close) * (1 + sp),
                         "low": np.minimum(open_, close) * (1 - sp), "close": close, "volume": 1.0})


def discover(frame, **kw):
    return run_discovery(frame, "1h", RiskConfig(), 10_000, 10, 2, time_budget_s=300, **kw)


def test_candidates_reproducible_and_valid():
    a, b = generate(80, seed=5), generate(80, seed=5)
    assert [c.spec.model_dump() for c in a] == [c.spec.model_dump() for c in b]
    assert len({c.family for c in a}) == 9
    assert generate(80, seed=6)[0].spec.model_dump() != a[0].spec.model_dump()
    assert all(c.spec.exit.all or c.spec.max_bars_in_trade for c in a)


def test_deflated_sharpe_behaviour():
    assert deflated_sharpe(0.0, 1, 0.0, 365, 0, 3) == pytest.approx(0.5)
    assert deflated_sharpe(0.2, 1, 0.0, 365, 0, 3) > 0.99
    # The same Sharpe is far less convincing after many trials.
    assert deflated_sharpe(0.1, 50, 0.0025, 365, 0, 3) < deflated_sharpe(0.1, 2, 0.0025, 365, 0, 3)


def test_effective_trials_counts_duplicates_once():
    r = np.random.default_rng(0)
    base = r.normal(size=300)
    dupes = [base + r.normal(scale=0.01, size=300) for _ in range(20)]
    indep = [r.normal(size=300) for _ in range(20)]
    assert effective_trials(dupes) < 1.5
    assert effective_trials(indep) > 15


@pytest.mark.parametrize("seed", [1, 2, 7])
def test_no_false_positive_on_random_walk(seed):
    out = discover(ar1_market(15_000, 0.0, seed), n_candidates=150, seed=7)
    assert out["funnel"]["robust"] == 0


def test_finds_planted_momentum_edge():
    out = discover(ar1_market(15_000, 0.3, 3), n_candidates=150, seed=7)
    robust = [r for r in out["results"] if r["verdict"] == "robust"]
    assert robust and all(r["holdout"]["monthly_pct"] > 0 for r in robust)
    assert all(r["holdout"]["daily_limit_breaches"] == 0 for r in robust)


def test_holdout_never_influences_selection():
    frame = ar1_market(15_000, 0.3, 3)
    tampered = frame.copy()
    cut = int(len(frame) * 0.8)
    tampered.loc[cut:, ["open", "high", "low", "close"]] *= np.linspace(1, 0.5, len(frame) - cut)[:, None]
    a, b = discover(frame, n_candidates=100, seed=3), discover(tampered, n_candidates=100, seed=3)
    assert [r["id"] for r in a["results"]] == [r["id"] for r in b["results"]]
    assert [r["validation"] for r in a["results"]] == [r["validation"] for r in b["results"]]
    assert [r["holdout"] for r in a["results"]] != [r["holdout"] for r in b["results"]]


def test_discover_endpoint_demo():
    client = TestClient(app)
    body = client.post("/api/discover", json={"bars": 3000, "n_candidates": 40}).json()
    assert body["synthetic_data"] is True and body["funnel"]["generated"] == 40
    assert set(body["segments"]) == {"train", "validation", "holdout"}
    assert body["risk"]["risk_per_trade_pct"] == 0.5
    for r in body["results"]:
        assert "_daily" not in r["train"] and r["rules"]["entry"]


def test_demo_data_discovery_finds_nothing_robust():
    out = discover(demo_ohlcv(bars=8000), n_candidates=120, seed=42)
    assert out["funnel"]["robust"] == 0
