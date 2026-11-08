import time

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.monte_carlo import MIN_TRADES, monte_carlo

client = TestClient(app)


def trades(returns_pct):
    return [{"return_pct": r} for r in returns_pct]


def test_too_few_trades():
    out = monte_carlo(trades([1.0] * (MIN_TRADES - 1)))
    assert out == {"enough": False, "trades": MIN_TRADES - 1, "min_trades": MIN_TRADES}


def test_deterministic_with_seed():
    t = trades([0.75, -0.5] * 30)
    assert monte_carlo(t) == monte_carlo(t)


def test_clear_edge_is_robust():
    # 60% winners at +0.75%, 40% losers at -0.5% (1:1.5 with 0.5% risk)
    out = monte_carlo(trades([0.75] * 60 + [-0.5] * 40))
    assert out["enough"] and out["runs"] == 10_000
    assert out["prob_edge_negative_pct"] < 5 and out["level"] == "good"
    assert 50 <= out["win_rate_pct"]["p50"] <= 70
    assert out["final_return_pct"]["p5"] < out["final_return_pct"]["p50"] < out["final_return_pct"]["p95"]
    assert out["max_drawdown_pct"]["p99"] <= out["max_drawdown_pct"]["p95"] <= out["max_drawdown_pct"]["p50"] <= 0
    assert sum(out["histogram"]["counts"]) == out["runs"]


def test_coin_flip_is_flagged():
    # breakeven win rate for 1:1.5 is 40%; at 40% the edge is zero
    out = monte_carlo(trades([0.75] * 40 + [-0.5] * 60))
    assert out["prob_edge_negative_pct"] > 20 and out["level"] == "bad"


def test_ruin_probability_uses_limit():
    out = monte_carlo(trades([3.0] * 25 + [-4.0] * 25), max_total_loss_pct=10)
    assert out["prob_total_loss_breach_pct"] > 50 and out["level"] == "bad"
    assert monte_carlo(trades([1.0] * 30), max_total_loss_pct=None)["prob_total_loss_breach_pct"] is None


def test_runs_capped_for_many_trades_and_fast():
    start = time.perf_counter()
    out = monte_carlo(trades([0.5, -0.3, 0.2] * 2000))
    assert out["runs"] * out["trades"] <= 6_000_000
    assert time.perf_counter() - start < 5


@pytest.mark.parametrize("path,body", [
    ("/api/backtests", {"strategy": "gold_trend_pullback", "bars": 3000,
                        "risk": {"risk_per_trade_pct": 0.5}}),
    ("/api/backtests", {"strategy": "ema_crossover", "bars": 2000, "include_trades": False}),
])
def test_api_returns_monte_carlo(path, body):
    data = client.post(path, json=body).json()
    mc = data["monte_carlo"]
    assert "enough" in mc
    if mc["enough"]:
        assert mc["trades"] == data["metrics"]["trades"] or not body.get("include_trades", True)
        assert mc["verdict"]
