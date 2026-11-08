"""Strategy discovery with overfitting guards.

Pipeline (chronological split, no shuffling):
    TRAIN (60%)  → every candidate is backtested; weak / rule-breaking ones are dropped.
    VALIDATION (20%) → the best train survivors must stay profitable and within the rules.
    HOLDOUT (20%) → the final few are measured ONCE. Holdout never influences selection;
                    it is the honest estimate of how the pick does on unseen data.

Selection bias is quantified with the Deflated Sharpe Ratio (Bailey & López de Prado,
2014): given how many strategies were tried and how much their Sharpe ratios varied, what
is the probability the winner's true Sharpe is above zero?

Signals are computed once on the full history (they only look backwards), then each
segment is backtested separately starting from the same capital.
"""

from __future__ import annotations

import math
import time
from statistics import NormalDist

import numpy as np
import pandas as pd

from app.discovery.candidates import Candidate, generate
from app.services.risk_engine import RiskConfig, run_risk_backtest
from app.services.timeframes import timeframe_minutes
from app.strategies.dsl import custom_signal, describe

EULER = 0.5772156649
N01 = NormalDist()


def deflated_sharpe(sr: float, n_trials: int, sr_variance: float, n_obs: int,
                    skew: float, kurt: float) -> float:
    """Probability that the true (per-period) Sharpe exceeds the best-of-N luck benchmark."""
    if n_obs < 3 or not math.isfinite(sr):
        return 0.0
    if n_trials > 1 and sr_variance > 0:
        sr0 = math.sqrt(sr_variance) * ((1 - EULER) * N01.inv_cdf(1 - 1 / n_trials)
                                        + EULER * N01.inv_cdf(1 - 1 / (n_trials * math.e)))
    else:
        sr0 = 0.0
    denom = 1 - skew * sr + (kurt - 1) / 4 * sr * sr
    if denom <= 0:
        return 0.0
    return float(N01.cdf((sr - sr0) * math.sqrt(n_obs - 1) / math.sqrt(denom)))


def _moments(x: np.ndarray) -> tuple[float, float, float]:
    """Sharpe (per period), skewness, kurtosis (non-excess)."""
    if len(x) < 3 or np.std(x) == 0:
        return 0.0, 0.0, 3.0
    m, s = float(np.mean(x)), float(np.std(x, ddof=1))
    z = (x - m) / np.std(x)
    return m / s, float(np.mean(z ** 3)), float(np.mean(z ** 4))


def effective_trials(returns: list[np.ndarray]) -> float:
    """Number of effectively independent trials: participation ratio of the eigenvalues of
    the trials' return-correlation matrix. Near-duplicate candidates count as one."""
    rows = [r for r in returns if len(r) > 2 and np.std(r) > 0]
    if len(rows) < 2:
        return float(max(1, len(rows)))
    length = min(len(r) for r in rows)
    with np.errstate(divide="ignore", invalid="ignore"):
        corr = np.nan_to_num(np.corrcoef(np.array([r[:length] for r in rows])))
    lam = np.clip(np.linalg.eigvalsh(corr), 0, None)
    return float(max(1.0, lam.sum() ** 2 / (lam ** 2).sum()))


def _monthly(final: float, initial: float, days: float) -> float:
    if days <= 0 or final <= 0:
        return -100.0
    return ((final / initial) ** (30.0 / days) - 1) * 100


class Discovery:
    def __init__(self, frame: pd.DataFrame, timeframe: str, risk: RiskConfig,
                 initial_capital: float, fee_bps: float, slippage_bps: float):
        self.frame = frame.reset_index(drop=True)
        self.timeframe = timeframe
        self.risk = risk
        self.capital = initial_capital
        self.fee, self.slip = fee_bps, slippage_bps
        n = len(self.frame)
        a, b = int(n * 0.6), int(n * 0.8)
        self.segments = {"train": (0, a), "validation": (a, b), "holdout": (b, n)}

    def _days(self, seg: str) -> float:
        a, b = self.segments[seg]
        ts = self.frame["timestamp"]
        span = (ts.iloc[b - 1] - ts.iloc[a]).total_seconds() / 86400
        return span + timeframe_minutes(self.timeframe) / 1440

    def evaluate(self, cand: Candidate, signal: pd.Series, seg: str) -> dict:
        a, b = self.segments[seg]
        risk = RiskConfig(**{**self.risk.__dict__, "stop_type": "atr",
                             "stop_atr_mult": cand.stop_atr_mult,
                             "take_profit_rr": cand.take_profit_rr})
        res = run_risk_backtest(self.frame.iloc[a:b], signal.iloc[a:b], risk, self.timeframe,
                                self.capital, self.fee, self.slip, return_daily=True)
        m, rk = res["metrics"], res["risk"]
        return {
            "monthly_pct": round(_monthly(m["final_equity"], self.capital, self._days(seg)), 4),
            "net_return_pct": m["net_return_pct"], "trades": m["trades"],
            "win_rate_pct": m["win_rate_pct"], "profit_factor": m["profit_factor"],
            "max_drawdown_pct": m["max_drawdown_pct"], "worst_day_pct": rk["worst_day_pct"],
            "daily_limit_breaches": rk["daily_limit_breaches"],
            "account_failed": rk["account_failed"], "avg_r": rk["avg_r_multiple"],
            "_daily": res["daily_returns"],
        }

    def buy_and_hold(self, seg: str) -> float:
        a, b = self.segments[seg]
        c = self.frame["close"]
        return round(_monthly(float(c.iloc[b - 1]), float(c.iloc[a]), self._days(seg)), 4)


def _ok(r: dict, min_trades: int, min_pf: float) -> bool:
    return (r["trades"] >= min_trades and not r["account_failed"]
            and r["daily_limit_breaches"] == 0 and (r["profit_factor"] or 0) >= min_pf
            and r["monthly_pct"] > 0)


def _public(r: dict) -> dict:
    return {k: v for k, v in r.items() if not k.startswith("_")}


def run_discovery(frame: pd.DataFrame, timeframe: str, risk: RiskConfig, initial_capital: float,
                  fee_bps: float, slippage_bps: float, n_candidates: int = 150, seed: int = 42,
                  time_budget_s: float = 45.0, finalists: int = 5,
                  families: list[str] | None = None) -> dict:
    started = time.monotonic()
    disc = Discovery(frame, timeframe, risk, initial_capital, fee_bps, slippage_bps)
    intraday = timeframe_minutes(timeframe) <= 60
    candidates = generate(n_candidates, seed, intraday=intraday, families=families)

    bars_train = disc.segments["train"][1]
    min_train = max(20, bars_train // 150)
    min_val = max(8, min_train // 3)

    signals: dict[str, pd.Series] = {}
    train: dict[str, dict] = {}
    train_sharpes: list[float] = []
    evaluated = 0
    for cand in candidates:
        if time.monotonic() - started > time_budget_s * 0.7:
            break
        sig = custom_signal(cand.spec, disc.frame, apply_stops=False)
        signals[cand.id] = sig
        r = disc.evaluate(cand, sig, "train")
        evaluated += 1
        train[cand.id] = r
        train_sharpes.append(_moments(r["_daily"])[0])

    by_id = {c.id: c for c in candidates}
    stage1 = [cid for cid, r in train.items() if _ok(r, min_train, 1.05)]
    stage1.sort(key=lambda cid: train[cid]["monthly_pct"] - 0.05 * abs(train[cid]["max_drawdown_pct"]),
                reverse=True)
    stage1 = stage1[:25]

    val: dict[str, dict] = {}
    for cid in stage1:
        val[cid] = disc.evaluate(by_id[cid], signals[cid], "validation")
    stage2 = [cid for cid in stage1 if _ok(val[cid], min_val, 1.0)]
    stage2.sort(key=lambda cid: min(train[cid]["monthly_pct"], val[cid]["monthly_pct"]),
                reverse=True)

    sr_var = float(np.var(train_sharpes, ddof=1)) if len(train_sharpes) > 1 else 0.0
    n_eff = effective_trials([r["_daily"] for r in train.values()])
    results = []
    for cid in stage2[:finalists]:
        hold = disc.evaluate(by_id[cid], signals[cid], "holdout")
        sel = np.concatenate([train[cid]["_daily"], val[cid]["_daily"]])
        sr, sk, ku = _moments(sel)
        dsr = deflated_sharpe(sr, max(2, round(n_eff)), sr_var, len(sel), sk, ku)
        hold_ok = (hold["monthly_pct"] > 0 and (hold["profit_factor"] or 0) >= 1.0
                   and not hold["account_failed"] and hold["daily_limit_breaches"] == 0)
        if hold_ok and dsr >= 0.9 and hold["trades"] >= min_val:
            verdict, label = "robust", "Sağlam aday"
        elif hold_ok:
            verdict, label = "promising", "Umut verici (şans etkisi dışlanamıyor)"
        else:
            verdict, label = "failed_holdout", "Görmediği veride başarısız"
        cand = by_id[cid]
        results.append({
            "id": cid, "family": cand.family, "name": cand.spec.name,
            "spec": cand.spec.model_dump(exclude_none=True), "rules": describe(cand.spec),
            "risk": {"stop_type": "atr", "stop_atr_mult": cand.stop_atr_mult,
                     "take_profit_rr": cand.take_profit_rr},
            "train": _public(train[cid]), "validation": _public(val[cid]), "holdout": _public(hold),
            "deflated_sharpe_prob": round(dsr, 4),
            "annual_sharpe_selection": round(sr * math.sqrt(365), 3),
            "verdict": verdict, "verdict_label": label,
        })

    ts = disc.frame["timestamp"]
    seg_info = {k: {"start": ts.iloc[a].isoformat(), "end": ts.iloc[b - 1].isoformat(),
                    "bars": b - a, "buy_and_hold_monthly_pct": disc.buy_and_hold(k)}
                for k, (a, b) in disc.segments.items()}
    return {
        "funnel": {"generated": len(candidates), "evaluated": evaluated,
                   "passed_train": len([c for c in train if _ok(train[c], min_train, 1.05)]),
                   "validated": len(stage2), "finalists": len(results),
                   "robust": sum(r["verdict"] == "robust" for r in results)},
        "thresholds": {"min_trades_train": min_train, "min_trades_validation": min_val,
                       "train_min_profit_factor": 1.05, "deflated_sharpe_min": 0.9,
                       "effective_trials": round(n_eff, 1)},
        "segments": seg_info,
        "results": results,
        "seed": seed,
        "families": families or "all",
        "elapsed_s": round(time.monotonic() - started, 2),
        "time_budget_hit": evaluated < len(candidates),
    }
