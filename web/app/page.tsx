"use client";

import { useState } from "react";
import EquityChart from "@/components/EquityChart";
import PriceChart from "@/components/PriceChart";
import { runBacktest } from "@/lib/api";
import { fmt, pct, shortTime, tone } from "@/lib/format";
import type { BacktestParams, BacktestResult, RunRecord } from "@/lib/types";

const DEFAULTS: BacktestParams = {
  data_source: "binance",
  symbol: "BTC/USDT",
  timeframe: "1h",
  bars: 1000,
  fast_period: 12,
  slow_period: 26,
  initial_capital: 10000,
  fee_bps: 10,
  slippage_bps: 2,
};

const TIMEFRAMES = ["15m", "30m", "1h", "4h", "1d"];

export default function Home() {
  const [params, setParams] = useState<BacktestParams>(DEFAULTS);
  const [result, setResult] = useState<BacktestResult | null>(null);
  const [runs, setRuns] = useState<RunRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showAllTrades, setShowAllTrades] = useState(false);

  const set = <K extends keyof BacktestParams>(key: K, value: BacktestParams[K]) =>
    setParams((p) => ({ ...p, [key]: value }));

  async function onRun(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const res = await runBacktest(params);
      setResult(res);
      setShowAllTrades(false);
      setRuns((r) => [{ id: Date.now(), params: { ...params }, result: res }, ...r].slice(0, 12));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }

  const num = (key: keyof BacktestParams) => (e: React.ChangeEvent<HTMLInputElement>) =>
    set(key, Number(e.target.value) as never);

  const m = result?.metrics;
  const trades = result ? [...result.trades].reverse() : [];

  return (
    <main className="shell">
      <header className="top">
        <div>
          <h1>Strategy Lab</h1>
          <p className="muted">Backtest araştırma paneli · canlı işlem kapalı</p>
        </div>
        {result && <span className="tag">motor v{result.engine_version}</span>}
      </header>

      <div className="layout">
        <form className="panel params" onSubmit={onRun}>
          <h2>Strateji</h2>
          <label>Strateji
            <select value="ema_crossover" disabled><option value="ema_crossover">EMA Crossover (long/flat)</option></select>
          </label>
          <label>Veri kaynağı
            <select value={params.data_source} onChange={(e) => set("data_source", e.target.value as BacktestParams["data_source"])}>
              <option value="binance">Binance (gerçek veri)</option>
              <option value="demo">Demo (sentetik)</option>
            </select>
          </label>
          <div className="row">
            <label>Sembol
              <input value={params.symbol} onChange={(e) => set("symbol", e.target.value.toUpperCase())} />
            </label>
            <label>Zaman dilimi
              <select value={params.timeframe} onChange={(e) => set("timeframe", e.target.value)}>
                {TIMEFRAMES.map((t) => <option key={t}>{t}</option>)}
              </select>
            </label>
          </div>
          <label>Bar sayısı
            <input type="number" min={100} max={5000} step={100} value={params.bars} onChange={num("bars")} />
          </label>
          <div className="row">
            <label>Hızlı EMA<input type="number" min={2} value={params.fast_period} onChange={num("fast_period")} /></label>
            <label>Yavaş EMA<input type="number" min={3} value={params.slow_period} onChange={num("slow_period")} /></label>
          </div>
          <h2>Maliyet ve sermaye</h2>
          <label>Başlangıç sermayesi
            <input type="number" min={1} value={params.initial_capital} onChange={num("initial_capital")} />
          </label>
          <div className="row">
            <label>Komisyon (bps)<input type="number" min={0} step={0.5} value={params.fee_bps} onChange={num("fee_bps")} /></label>
            <label>Slippage (bps)<input type="number" min={0} step={0.5} value={params.slippage_bps} onChange={num("slippage_bps")} /></label>
          </div>
          <p className="hint">1 bps = %0.01. Binance spot taker komisyonu ≈ 10 bps.</p>
          <button type="submit" disabled={loading}>{loading ? "Çalışıyor…" : "Backtest çalıştır"}</button>
          {error && <p className="error">{error}</p>}
        </form>

        <section className="results">
          {!result && !loading && (
            <div className="panel empty">
              <h2>Henüz sonuç yok</h2>
              <p className="muted">Soldaki parametreleri seçip backtest çalıştır. Sinyal mum kapanışında, emir bir sonraki mumun açılışında gerçekleşir; komisyon ve slippage dahildir.</p>
            </div>
          )}

          {result && m && (
            <>
              {result.synthetic_data ? (
                <div className="banner warn">Sentetik demo verisi — bu sonuçlar gerçek piyasa performansı değildir.</div>
              ) : (
                <div className="banner">
                  {result.data_source} · {result.symbol} {result.timeframe} · {shortTime(result.data_quality.start ?? "")} → {shortTime(result.data_quality.end ?? "")} · {result.data_quality.bars} bar
                  {result.data_quality.gaps > 0 && <> · <b>{result.data_quality.missing_bars_estimate} eksik bar</b></>}
                </div>
              )}

              <div className="metrics">
                <Metric label="Net getiri" value={pct(m.net_return_pct)} cls={tone(m.net_return_pct)} sub={`${fmt(m.net_profit)} · son ${fmt(m.final_equity)}`} />
                <Metric label="Max drawdown" value={pct(m.max_drawdown_pct)} cls="neg" sub={`Recovery ${fmt(m.recovery_factor)}`} />
                <Metric label="İşlem" value={String(m.trades)} sub={`Win rate %${fmt(m.win_rate_pct, 1)}`} />
                <Metric label="Profit factor" value={fmt(m.profit_factor)} cls={m.profit_factor !== null ? tone(m.profit_factor - 1) : ""} sub={`Ort. işlem ${pct(m.avg_trade_return_pct)}`} />
                <Metric label="Sharpe (yıllık)" value={fmt(m.sharpe_annualized)} cls={tone(m.sharpe_annualized)} sub={`Piyasada %${fmt(m.exposure_pct, 1)}`} />
                <Metric label="Kazanç / kayıp" value={fmt(m.avg_win_loss_ratio)} sub={`Ardışık zarar ${m.max_consecutive_losses}`} />
                <Metric label="Toplam komisyon" value={fmt(m.total_fees)} sub="slippage fiyatta" />
                <Metric label="Beklenti / işlem" value={fmt(m.expectancy_per_trade)} cls={tone(m.expectancy_per_trade)} />
              </div>

              <div className="panel">
                <div className="panel-head">
                  <h2>Fiyat ve işlemler</h2>
                  <span className="legend"><i style={{ background: "#f2b84b" }} />EMA {result.parameters.fast_period}<i style={{ background: "#6aa8ff" }} />EMA {result.parameters.slow_period}</span>
                </div>
                <PriceChart candles={result.candles} trades={result.trades} />
              </div>

              <div className="panel">
                <div className="panel-head"><h2>Equity ve drawdown</h2></div>
                <EquityChart curve={result.equity_curve} />
              </div>

              {result.monthly_returns_pct.length > 1 && (
                <div className="panel">
                  <div className="panel-head"><h2>Aylık getiri</h2></div>
                  <div className="months">
                    {result.monthly_returns_pct.map((mo) => (
                      <div key={mo.month} className={`month ${tone(mo.return_pct)}`}>
                        <span>{mo.month}</span><b>{pct(mo.return_pct, 1)}</b>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              <div className="panel">
                <div className="panel-head">
                  <h2>İşlem listesi</h2>
                  <span className="muted">{trades.length} işlem, en yenisi üstte</span>
                </div>
                <div className="table-wrap">
                  <table>
                    <thead><tr><th>Giriş</th><th>Çıkış</th><th className="r">Giriş fiyatı</th><th className="r">Çıkış fiyatı</th><th className="r">Bar</th><th className="r">Getiri</th><th className="r">K/Z</th></tr></thead>
                    <tbody>
                      {(showAllTrades ? trades : trades.slice(0, 25)).map((t) => (
                        <tr key={t.entry_time}>
                          <td>{shortTime(t.entry_time)}</td>
                          <td>{shortTime(t.exit_time)}{t.forced_exit && <span className="tag sm">açık</span>}</td>
                          <td className="r">{fmt(t.entry_price, 4)}</td>
                          <td className="r">{fmt(t.exit_price, 4)}</td>
                          <td className="r">{t.bars_held}</td>
                          <td className={`r ${tone(t.return_pct)}`}>{pct(t.return_pct)}</td>
                          <td className={`r ${tone(t.pnl)}`}>{fmt(t.pnl)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {trades.length > 25 && (
                  <button type="button" className="ghost" onClick={() => setShowAllTrades((v) => !v)}>
                    {showAllTrades ? "Daha az göster" : `Tümünü göster (${trades.length})`}
                  </button>
                )}
              </div>
            </>
          )}

          {runs.length > 1 && (
            <div className="panel">
              <div className="panel-head"><h2>Çalıştırma karşılaştırması</h2><span className="muted">bu oturum</span></div>
              <div className="table-wrap">
                <table>
                  <thead><tr><th>Kaynak</th><th>Sembol</th><th>TF</th><th>EMA</th><th className="r">Getiri</th><th className="r">Max DD</th><th className="r">İşlem</th><th className="r">Win %</th><th className="r">PF</th><th className="r">Sharpe</th></tr></thead>
                  <tbody>
                    {runs.map((r) => (
                      <tr key={r.id} className={r.result === result ? "active" : ""} onClick={() => setResult(r.result)}>
                        <td>{r.result.synthetic_data ? "demo" : "binance"}</td>
                        <td>{r.params.symbol}</td>
                        <td>{r.params.timeframe}</td>
                        <td>{r.params.fast_period}/{r.params.slow_period}</td>
                        <td className={`r ${tone(r.result.metrics.net_return_pct)}`}>{pct(r.result.metrics.net_return_pct, 1)}</td>
                        <td className="r neg">{pct(r.result.metrics.max_drawdown_pct, 1)}</td>
                        <td className="r">{r.result.metrics.trades}</td>
                        <td className="r">{fmt(r.result.metrics.win_rate_pct, 1)}</td>
                        <td className="r">{fmt(r.result.metrics.profit_factor)}</td>
                        <td className="r">{fmt(r.result.metrics.sharpe_annualized)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          <p className="disclaimer">Geçmiş performans gelecekteki sonuçları garanti etmez. Bu panel yalnızca araştırma içindir; out-of-sample ve walk-forward doğrulaması henüz yapılmamıştır.</p>
        </section>
      </div>
    </main>
  );
}

function Metric({ label, value, sub, cls = "" }: { label: string; value: string; sub?: string; cls?: string }) {
  return (
    <div className="metric">
      <span className="label">{label}</span>
      <b className={cls}>{value}</b>
      {sub && <span className="sub">{sub}</span>}
    </div>
  );
}
