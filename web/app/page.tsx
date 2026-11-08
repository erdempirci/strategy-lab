"use client";

import { useEffect, useMemo, useState } from "react";
import { CustomEditor, RulesCard } from "@/components/CustomStrategy";
import { DeskSide, DeskView } from "@/components/Desk";
import { DiscoveryFields, DiscoveryReport } from "@/components/Discovery";
import EquityChart from "@/components/EquityChart";
import { RISK_DEFAULTS, RiskFields, RiskReport } from "@/components/RiskPanel";
import PriceChart from "@/components/PriceChart";
import { OVERLAY_PALETTE } from "@/components/charts";
import { compareAll, discover, fetchStrategies, runBacktest, runCustom } from "@/lib/api";
import { useCustomStrategy } from "@/lib/useCustomStrategy";
import { useDesk } from "@/lib/useDesk";
import { fmt, pct, shortTime, tone } from "@/lib/format";
import type {
  BacktestResult, CompareResult, DiscoveryCandidate, DiscoveryResult, MarketParams, RunRecord,
  StrategyJSON, StrategySpec,
} from "@/lib/types";

const MARKET_DEFAULTS: MarketParams = {
  data_source: "binance",
  symbol: "BTC/USDT",
  timeframe: "1h",
  bars: 1000,
  initial_capital: 10000,
  fee_bps: 10,
  slippage_bps: 2,
  risk: RISK_DEFAULTS,
};

const TIMEFRAMES = ["15m", "30m", "1h", "4h", "1d"];
const YAHOO_TIMEFRAMES = ["1h", "2h", "4h", "1d"];
const EXIT_TR: Record<string, string> = {
  signal: "sinyal", stop: "stop", target: "hedef", daily_limit: "günlük limit",
  max_total_loss: "toplam limit", end_of_data: "veri sonu",
};

export default function Home() {
  const [mode, setMode] = useState<"library" | "custom" | "discover" | "desk">("library");
  const [discN, setDiscN] = useState(150);
  const [discSeed, setDiscSeed] = useState(42);
  const [discFamily, setDiscFamily] = useState("");
  const [discBars, setDiscBars] = useState(10000);
  const [discovery, setDiscovery] = useState<DiscoveryResult | null>(null);
  const custom = useCustomStrategy();
  const desk = useDesk(mode === "desk");
  const [market, setMarket] = useState<MarketParams>(MARKET_DEFAULTS);
  const [strategies, setStrategies] = useState<StrategySpec[]>([]);
  const [strategyId, setStrategyId] = useState("ema_crossover");
  const [paramValues, setParamValues] = useState<Record<string, Record<string, number>>>({});
  const [result, setResult] = useState<BacktestResult | null>(null);
  const [runs, setRuns] = useState<RunRecord[]>([]);
  const [comparison, setComparison] = useState<CompareResult | null>(null);
  const [loading, setLoading] = useState<"run" | "compare" | "discover" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showAllTrades, setShowAllTrades] = useState(false);

  useEffect(() => {
    fetchStrategies()
      .then((list) => {
        setStrategies(list);
        setParamValues(Object.fromEntries(list.map((s) =>
          [s.id, Object.fromEntries(s.params.map((p) => [p.name, p.default]))])));
      })
      .catch((e) => setError(`Strateji listesi alınamadı: ${e.message}`));
  }, []);

  const spec = strategies.find((s) => s.id === strategyId);
  const values = paramValues[strategyId] || {};

  const setM = <K extends keyof MarketParams>(key: K, value: MarketParams[K]) =>
    setMarket((m) => ({ ...m, [key]: value }));
  // Switching to/from US futures also switches to sensible symbol, timeframe and costs.
  const pickSource = (source: MarketParams["data_source"]) =>
    setMarket((m) => {
      if (source === "yahoo") {
        return { ...m, data_source: source, symbol: ["US100", "US500"].includes(m.symbol) ? m.symbol : "US100",
                 timeframe: YAHOO_TIMEFRAMES.includes(m.timeframe) ? m.timeframe : "1h",
                 fee_bps: 0.5, slippage_bps: 0.5 };
      }
      if (m.data_source === "yahoo") {
        return { ...m, data_source: source, symbol: "BTC/USDT", fee_bps: MARKET_DEFAULTS.fee_bps,
                 slippage_bps: MARKET_DEFAULTS.slippage_bps };
      }
      return { ...m, data_source: source };
    });
  const numM = (key: keyof MarketParams) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setM(key, Number(e.target.value) as never);
  const setP = (name: string, value: number) =>
    setParamValues((pv) => ({ ...pv, [strategyId]: { ...pv[strategyId], [name]: value } }));
  const resetParams = () => spec && setParamValues((pv) => ({
    ...pv, [strategyId]: Object.fromEntries(spec.params.map((p) => [p.name, p.default])),
  }));

  async function run(id = strategyId, params = values, forceLibrary = false,
                     override?: { spec: StrategyJSON; market: MarketParams }) {
    setLoading("run");
    setError(null);
    try {
      const res = override
        ? await runCustom(override.market, override.spec)
        : !forceLibrary && mode === "custom" && custom.spec
          ? await runCustom(market, custom.spec)
          : await runBacktest(market, id, params);
      setResult(res);
      setShowAllTrades(false);
      const label = res.strategy === "custom" ? "özel kurallar" : Object.values(res.parameters).join("/");
      setRuns((r) => [{ id: Date.now(), label, result: res }, ...r].slice(0, 15));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(null);
    }
  }

  async function runDiscovery() {
    setLoading("discover");
    setError(null);
    try {
      setDiscovery(await discover({ ...market, bars: discBars }, discN, discSeed,
        discFamily ? [discFamily] : undefined));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(null);
    }
  }

  async function openCandidate(c: DiscoveryCandidate) {
    const note = `Keşif motorundan (${c.verdict_label}): son dönemde aylık ${c.holdout.monthly_pct.toFixed(2)}%, ` +
      `şans testi %${Math.round(c.deflated_sharpe_prob * 100)}. Aşağıdaki backtest eğitim dönemi DAHİL tüm veriyi kapsar.`;
    const loaded = await custom.loadSpec(c.spec, note);
    if (!loaded) return;
    const nextMarket: MarketParams = {
      ...market, bars: discBars,
      risk: { ...(market.risk ?? RISK_DEFAULTS), stop_type: "atr", stop_atr_mult: c.risk.stop_atr_mult,
              take_profit_rr: c.risk.take_profit_rr },
    };
    setMarket(nextMarket);
    setMode("custom");
    await run(strategyId, values, false, { spec: loaded, market: nextMarket });
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  async function compare() {
    setLoading("compare");
    setError(null);
    try {
      setComparison(await compareAll(market));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(null);
    }
  }

  function openFromCompare(id: string) {
    const s = strategies.find((x) => x.id === id);
    if (!s) return;
    const defaults = Object.fromEntries(s.params.map((p) => [p.name, p.default]));
    setMode("library");
    setStrategyId(id);
    setParamValues((pv) => ({ ...pv, [id]: defaults }));
    run(id, defaults, true);
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  const compareRows = useMemo(() => {
    if (!comparison) return [];
    return [...comparison.results].sort((a, b) =>
      (b.metrics?.net_return_pct ?? -Infinity) - (a.metrics?.net_return_pct ?? -Infinity));
  }, [comparison]);

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
        <form className="panel params" onSubmit={(e) => { e.preventDefault(); run(); }}>
          <div className="tabs" role="tablist">
            <button type="button" role="tab" aria-selected={mode === "library"} className={mode === "library" ? "on" : ""} onClick={() => setMode("library")}>Hazır</button>
            <button type="button" role="tab" aria-selected={mode === "custom"} className={mode === "custom" ? "on" : ""} onClick={() => setMode("custom")}>Kendi</button>
            <button type="button" role="tab" aria-selected={mode === "discover"} className={mode === "discover" ? "on" : ""} onClick={() => setMode("discover")}>Keşfet</button>
            <button type="button" role="tab" aria-selected={mode === "desk"} className={mode === "desk" ? "on" : ""} onClick={() => setMode("desk")}>Masa</button>
          </div>
          {mode === "desk" ? <DeskSide desk={desk} /> : (<>
          {mode === "discover" ? <DiscoveryFields n={discN} seed={discSeed} onN={setDiscN} onSeed={setDiscSeed} family={discFamily} onFamily={setDiscFamily} />
          : mode === "custom" ? <CustomEditor c={custom} /> : (<>
          <label>Strateji
            <select value={strategyId} onChange={(e) => setStrategyId(e.target.value)}>
              {strategies.length === 0 && <option>Yükleniyor…</option>}
              {strategies.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
          </label>
          {spec && <p className="hint">{spec.description}</p>}
          {spec && (
            <div className="row">
              {spec.params.map((p) => (
                <label key={p.name}>{p.label}
                  <input type="number" min={p.min} max={p.max} step={p.step}
                    value={values[p.name] ?? p.default}
                    onChange={(e) => setP(p.name, Number(e.target.value))} />
                </label>
              ))}
            </div>
          )}
          {spec && <button type="button" className="link" onClick={resetParams}>Varsayılanlara dön</button>}
          </>)}

          <h2>Piyasa</h2>
          <label>Veri kaynağı
            <select value={market.data_source} onChange={(e) => pickSource(e.target.value as MarketParams["data_source"])}>
              <option value="binance">Kripto · Binance</option>
              <option value="yahoo">US vadeli · Yahoo (US100, US500)</option>
              <option value="demo">Demo (sentetik)</option>
            </select>
          </label>
          {market.data_source === "yahoo" && (
            <p className="hint">US100 = Nasdaq 100 vadeli (NQ), US500 = S&amp;P 500 vadeli (ES). Haftada 5 gün, günde ~23 saat işlem görür; veri ~10 dk gecikmeli, 1h ve üstü zaman dilimi. Masa yalnızca uzun (alış) işlem açar.</p>
          )}
          <div className="row">
            <label>Sembol
              {market.data_source === "yahoo" ? (
                <select value={market.symbol} onChange={(e) => setM("symbol", e.target.value)}>
                  <option value="US100">US100</option>
                  <option value="US500">US500</option>
                </select>
              ) : <input value={market.symbol} onChange={(e) => setM("symbol", e.target.value.toUpperCase())} />}
            </label>
            <label>Zaman dilimi
              <select value={market.timeframe} onChange={(e) => setM("timeframe", e.target.value)}>
                {(market.data_source === "yahoo" ? YAHOO_TIMEFRAMES : TIMEFRAMES).map((t) => <option key={t}>{t}</option>)}
              </select>
            </label>
          </div>
          <label>Bar sayısı
            {mode === "discover"
              ? <input type="number" min={2000} max={20000} step={1000} value={discBars} onChange={(e) => setDiscBars(Number(e.target.value))} />
              : <input type="number" min={100} max={20000} step={100} value={market.bars} onChange={numM("bars")} />}
          </label>

          <h2>Maliyet ve sermaye</h2>
          <label>Başlangıç sermayesi
            <input type="number" min={1} value={market.initial_capital} onChange={numM("initial_capital")} />
          </label>
          <div className="row">
            <label>Komisyon (bps)<input type="number" min={0} step={0.5} value={market.fee_bps} onChange={numM("fee_bps")} /></label>
            <label>Slippage (bps)<input type="number" min={0} step={0.5} value={market.slippage_bps} onChange={numM("slippage_bps")} /></label>
          </div>
          <p className="hint">1 bps = %0.01. Binance spot taker komisyonu ≈ 10 bps; NQ/ES vadelide komisyon + 1 tick kayma toplamı ≈ 0,2 bps (0,5 + 0,5 temkinli). Seans saatleri UTC.</p>
          <h2>Risk kuralları</h2>
          <RiskFields risk={market.risk ?? null} onChange={(r) => setM("risk", r)} />
          {mode === "discover" ? (
            <button type="button" disabled={!!loading} onClick={runDiscovery}>{loading === "discover" ? "Stratejiler aranıyor…" : "Strateji ara"}</button>
          ) : (
            <button type="submit" disabled={!!loading || (mode === "custom" ? !custom.spec : !spec)}>{loading === "run" ? "Çalışıyor…" : "Backtest çalıştır"}</button>
          )}
          {mode !== "discover" && (
            <button type="button" className="ghost" disabled={!!loading} onClick={compare}>
              {loading === "compare" ? "Karşılaştırılıyor…" : "Tüm stratejileri karşılaştır"}
            </button>
          )}
          {error && <p className="error">{error}</p>}
          </>)}
        </form>

        <section className="results">
          {mode === "desk" && <DeskView desk={desk} />}
          {mode === "discover" && discovery && (
            <DiscoveryReport d={discovery} onOpen={openCandidate} added={desk.added} busy={desk.busy}
              onAdd={(c) => desk.addFromDiscovery(discovery, c)} />
          )}
          {mode === "discover" && (desk.notice || desk.error) && (
            <div className={`banner ${desk.error ? "warn" : ""}`}>{desk.error ?? desk.notice}</div>
          )}
          {mode === "discover" && !discovery && !loading && (
            <div className="panel empty">
              <h2>Strateji keşfi</h2>
              <p className="muted">&quot;Strateji ara&quot;ya bas: sistem risk kurallarınla (işlem başı risk, günlük ve toplam kayıp limiti) yüzlerce stratejiyi dener, overfitting&apos;e karşı üç aşamada eler ve sadece görmediği veride de ayakta kalanları gösterir. Sonuç &quot;hiçbir şey bulunamadı&quot; da olabilir — bu da değerli bir bilgidir.</p>
            </div>
          )}
          {mode === "custom" && <RulesCard c={custom} />}
          {mode !== "desk" && comparison && (
            <div className="panel">
              <div className="panel-head">
                <h2>Strateji karşılaştırması</h2>
                <span className="muted">
                  {comparison.symbol} {comparison.timeframe} · {comparison.data_quality.bars} bar · varsayılan parametreler
                </span>
              </div>
              {comparison.synthetic_data && <p className="warn-text">Sentetik demo verisi — gerçek performans değildir.</p>}
              <div className="table-wrap">
                <table>
                  <thead><tr><th>#</th><th>Strateji</th><th className="r">Getiri</th><th className="r">Max DD</th><th className="r">İşlem</th><th className="r">Win %</th><th className="r">PF</th><th className="r">Sharpe</th><th className="r">Piyasada</th>{comparison.results.some((r) => r.risk) && <><th className="r">En kötü gün</th><th className="r">Ay ort.</th><th>Kurallar</th></>}</tr></thead>
                  <tbody>
                    {compareRows.map((r, i) => (
                      <tr key={r.strategy} className={r.metrics ? "click" : "disabled"}
                        onClick={() => r.metrics && openFromCompare(r.strategy)}>
                        <td className="muted">{r.metrics ? i + 1 : "—"}</td>
                        <td>{r.name}</td>
                        {r.metrics ? (
                          <>
                            <td className={`r ${tone(r.metrics.net_return_pct)}`}>{pct(r.metrics.net_return_pct, 1)}</td>
                            <td className="r neg">{pct(r.metrics.max_drawdown_pct, 1)}</td>
                            <td className="r">{r.metrics.trades}</td>
                            <td className="r">{fmt(r.metrics.win_rate_pct, 1)}</td>
                            <td className="r">{fmt(r.metrics.profit_factor)}</td>
                            <td className={`r ${tone(r.metrics.sharpe_annualized)}`}>{fmt(r.metrics.sharpe_annualized)}</td>
                            <td className="r">%{fmt(r.metrics.exposure_pct, 0)}</td>
                            {r.risk && (
                              <>
                                <td className="r neg">{pct(r.risk.worst_day_pct, 2)}</td>
                                <td className={`r ${tone(r.risk.avg_monthly_return_pct)}`}>{pct(r.risk.avg_monthly_return_pct, 2)}</td>
                                <td>{r.risk.account_failed ? <span className="tag bad sm">patladı</span> : r.risk.daily_limit_breaches ? <span className="tag warn sm">{r.risk.daily_limit_breaches} ihlal</span> : <span className="tag good sm">uydu</span>}</td>
                              </>
                            )}
                          </>
                        ) : (
                          <td colSpan={10} className="muted">{r.error}</td>
                        )}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <p className="hint table-note">Varsayılan parametreler, yalnızca örneklem içi (in-sample) sonuç; henüz doğrulanmadı. Detay için bir satıra dokun.</p>
            </div>
          )}

          {!result && !loading && !comparison && mode === "library" && (
            <div className="panel empty">
              <h2>Henüz sonuç yok</h2>
              <p className="muted">Bir strateji seçip backtest çalıştır ya da tüm stratejileri aynı veride karşılaştır. Sinyal mum kapanışında, emir bir sonraki mumun açılışında gerçekleşir; komisyon ve slippage dahildir.</p>
            </div>
          )}

          {mode !== "desk" && result && m && (
            <>
              {result.synthetic_data ? (
                <div className="banner warn">Sentetik demo verisi — bu sonuçlar gerçek piyasa performansı değildir.</div>
              ) : (
                <div className="banner">
                  <b className="plain">{result.strategy_name}</b> · {result.data_source} · {result.symbol} {result.timeframe} · {shortTime(result.data_quality.start ?? "")} → {shortTime(result.data_quality.end ?? "")} · {result.data_quality.bars} bar
                  {result.data_quality.gaps > 0 && <> · <b>{result.data_quality.missing_bars_estimate} eksik bar</b></>}
                </div>
              )}

              {m.trades < 30 && (
                <div className="banner warn">Sadece {m.trades} işlem var — bu kadar az işlemle win rate, profit factor ve Sharpe istatistiksel olarak güvenilir değildir. Daha fazla bar veya daha kısa zaman dilimi dene.</div>
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

              {result.risk && <RiskReport info={result.risk} />}

              <div className="panel">
                <div className="panel-head">
                  <h2>Fiyat ve işlemler</h2>
                  <span className="legend">
                    {result.overlays.map((o, i) => (
                      <span key={o.name}><i style={{ background: o.color || OVERLAY_PALETTE[i % OVERLAY_PALETTE.length] }} />{o.name}</span>
                    ))}
                  </span>
                </div>
                <PriceChart candles={result.candles} overlays={result.overlays} trades={result.trades} />
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
                    <thead><tr><th>Giriş</th><th>Çıkış</th><th className="r">Giriş fiyatı</th><th className="r">Çıkış fiyatı</th><th className="r">Bar</th><th>Çıkış nedeni</th><th className="r">R</th><th className="r">Getiri</th><th className="r">K/Z</th></tr></thead>
                    <tbody>
                      {(showAllTrades ? trades : trades.slice(0, 25)).map((t) => (
                        <tr key={t.entry_time}>
                          <td>{shortTime(t.entry_time)}</td>
                          <td>{shortTime(t.exit_time)}{t.forced_exit && <span className="tag sm">açık</span>}</td>
                          <td className="r">{fmt(t.entry_price, 4)}</td>
                          <td className="r">{fmt(t.exit_price, 4)}</td>
                          <td className="r">{t.bars_held}</td>
                          <td className="muted">{EXIT_TR[t.exit_reason ?? "signal"] ?? t.exit_reason}</td>
                          <td className={`r ${tone(t.r_multiple ?? null)}`}>{t.r_multiple != null ? fmt(t.r_multiple) : "—"}</td>
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

          {mode !== "desk" && runs.length > 1 && (
            <div className="panel">
              <div className="panel-head"><h2>Çalıştırma geçmişi</h2><span className="muted">bu oturum · dokununca açılır</span></div>
              <div className="table-wrap">
                <table>
                  <thead><tr><th>Strateji</th><th>Parametre</th><th>Sembol</th><th>TF</th><th className="r">Getiri</th><th className="r">Max DD</th><th className="r">İşlem</th><th className="r">PF</th><th className="r">Sharpe</th></tr></thead>
                  <tbody>
                    {runs.map((r) => (
                      <tr key={r.id} className={r.result === result ? "click active" : "click"} onClick={() => setResult(r.result)}>
                        <td>{r.result.strategy_name}{r.result.synthetic_data && <span className="tag sm">demo</span>}</td>
                        <td>{r.label}</td>
                        <td>{r.result.symbol}</td>
                        <td>{r.result.timeframe}</td>
                        <td className={`r ${tone(r.result.metrics.net_return_pct)}`}>{pct(r.result.metrics.net_return_pct, 1)}</td>
                        <td className="r neg">{pct(r.result.metrics.max_drawdown_pct, 1)}</td>
                        <td className="r">{r.result.metrics.trades}</td>
                        <td className="r">{fmt(r.result.metrics.profit_factor)}</td>
                        <td className="r">{fmt(r.result.metrics.sharpe_annualized)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {mode !== "desk" && <p className="disclaimer">Geçmiş performans gelecekteki sonuçları garanti etmez. Bu panel yalnızca araştırma içindir. Hazır ve kendi stratejilerinin sonuçları örneklem içidir; görmediği veride sınamak için Keşfet sekmesini kullan.</p>}
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
