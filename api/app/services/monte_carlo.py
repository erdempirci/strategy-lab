"""Monte Carlo robustness check on a finished backtest ("Time Machine").

Idea adapted from the MIT-licensed day-trader-skill (Jake Schincariol): before risking
money, replay the outcome many times. Here we resample the strategy's OWN closed trades
with replacement (bootstrap), 10,000 times by default, each run the same length as the
backtest, and compound them. That answers:

- was the edge luck?   share of runs whose average trade is <= 0
- how bad can it get?  drawdown percentiles, worst losing streak
- will I break my rules? share of runs that touch the max-total-loss line

Limitations, stated in the payload: trades are treated as independent (no regime
clustering), and the daily loss limit is not modelled (trades, not days, are resampled).
Deterministic: a fixed seed, so the same backtest always gives the same numbers.
"""

from __future__ import annotations

import numpy as np

MIN_TRADES = 20
DEFAULT_RUNS = 10_000
SEED = 19
MAX_CELLS = 6_000_000          # runs × trades cap, keeps a request well under a second


def _streaks(losses: np.ndarray) -> np.ndarray:
    """Longest run of True per row."""
    best = np.zeros(losses.shape[0], dtype=int)
    cur = np.zeros(losses.shape[0], dtype=int)
    for j in range(losses.shape[1]):
        cur = np.where(losses[:, j], cur + 1, 0)
        best = np.maximum(best, cur)
    return best


def _pcts(x: np.ndarray, qs=(5, 25, 50, 75, 95)) -> dict:
    return {f"p{q}": round(float(np.percentile(x, q)), 2) for q in qs}


def monte_carlo(trades: list[dict], max_total_loss_pct: float | None = 10.0,
                runs: int = DEFAULT_RUNS, seed: int = SEED) -> dict:
    rets = np.array([t["return_pct"] for t in trades if t.get("return_pct") is not None],
                    dtype=float) / 100
    n = len(rets)
    if n < MIN_TRADES:
        return {"enough": False, "trades": n, "min_trades": MIN_TRADES}
    runs = int(max(100, min(runs, MAX_CELLS // n)))
    rng = np.random.default_rng(seed)
    sample = rets[rng.integers(0, n, size=(runs, n))]

    equity = np.cumprod(1 + sample, axis=1)
    final = (equity[:, -1] - 1) * 100
    peak = np.maximum.accumulate(np.concatenate([np.ones((runs, 1)), equity], axis=1), axis=1)[:, 1:]
    max_dd = ((equity / peak - 1).min(axis=1)) * 100            # negative numbers
    lowest = np.minimum(equity.min(axis=1), 1.0)
    win_rate = (sample > 0).mean(axis=1) * 100
    edge = sample.mean(axis=1)
    streak = _streaks(sample <= 0)

    p_edge_neg = float((edge <= 0).mean() * 100)
    p_loss = float((final < 0).mean() * 100)
    p_ruin = (float((lowest <= 1 - max_total_loss_pct / 100).mean() * 100)
              if max_total_loss_pct else None)

    if p_edge_neg < 5:
        verdict, level = "Kenar sağlam görünüyor: koşuların %95'inden fazlasında ortalama işlem kârda.", "good"
    elif p_edge_neg < 20:
        verdict, level = "Umut verici ama kesin değil: sonucun bir kısmı şans olabilir, daha fazla veriyle test et.", "warn"
    else:
        verdict, level = "Yazı-tura'ya yakın: bu sonuç şansla rahatça çıkabilir.", "bad"
    if p_ruin is not None and p_ruin >= 5:
        verdict += f" Dikkat: koşuların %{p_ruin:.0f}'inde toplam kayıp limiti (%{max_total_loss_pct:g}) aşılıyor."
        level = "bad" if p_ruin >= 20 else ("warn" if level == "good" else level)

    counts, edges = np.histogram(final, bins=24)
    return {
        "enough": True,
        "runs": runs,
        "trades": n,
        "seed": seed,
        "method": "bootstrap of closed trades (with replacement), compounded",
        "final_return_pct": _pcts(final),
        "max_drawdown_pct": {"p50": round(float(np.percentile(max_dd, 50)), 2),
                             "p95": round(float(np.percentile(max_dd, 5)), 2),    # worse 5%
                             "p99": round(float(np.percentile(max_dd, 1)), 2)},
        "win_rate_pct": _pcts(win_rate, (5, 50, 95)),
        "losing_streak": {"p50": int(np.percentile(streak, 50)),
                          "p95": int(np.percentile(streak, 95))},
        "prob_edge_negative_pct": round(p_edge_neg, 2),
        "prob_loss_pct": round(p_loss, 2),
        "prob_total_loss_breach_pct": round(p_ruin, 2) if p_ruin is not None else None,
        "max_total_loss_pct": max_total_loss_pct,
        "histogram": {"counts": counts.tolist(),
                      "edges": [round(float(e), 2) for e in edges]},
        "verdict": verdict,
        "level": level,
        "limitations": "İşlemler bağımsız varsayılır (piyasa rejimi kümelenmesi yok); "
                       "günlük kayıp limiti modellenmez.",
    }
