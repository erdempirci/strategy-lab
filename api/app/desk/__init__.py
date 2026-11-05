"""Trading desk ("Masa"): rule-based roles that forward-test discovered strategies on paper.

Roles (all deterministic, no LLM):
    Tarayıcı      scanner      — fresh entry signals of desk strategies + volume spikes
    Giriş-Çıkış   planner      — entry / stop / target from the strategy and the risk engine
    Risk          risk officer — portfolio-level sizing and loss limits (RED = blocked)
    Şüpheci       skeptic      — objections from evidence → AL / BEKLE / GEÇ
    Günlükçü      journal      — every decision and paper trade, weekly report

Paper trades are never simulated by a second engine: each run recomputes the strategy
with `run_risk_backtest` (the same engine discovery used) on a signal where only the
approved entries are kept. Closed trades are frozen in storage; open ones are re-marked.
No orders are ever sent anywhere.
"""
