"use client";

import { useEffect, useState } from "react";
import { fmt, pct, shortTime, tone } from "@/lib/format";
import type { DeskDecision, DeskSettings } from "@/lib/types";
import type { Desk } from "@/lib/useDesk";

const VERDICT_CLS = { AL: "good", BEKLE: "warn", "GEÇ": "bad" } as const;
const EXIT_TR: Record<string, string> = {
  signal: "sinyal", stop: "stop", target: "hedef", end_of_data: "açık",
};

export function DeskSide({ desk }: { desk: Desk }) {
  const st = desk.state;
  const [s, setS] = useState<DeskSettings | null>(null);
  const [watch, setWatch] = useState("");
  useEffect(() => {
    if (st && !s) { setS(st.settings); setWatch(st.settings.watchlist.join(", ")); }
  }, [st, s]);
  const num = (k: keyof DeskSettings) => (e: React.ChangeEvent<HTMLInputElement>) =>
    s && setS({ ...s, [k]: Number(e.target.value) });

  return (
    <>
      <p className="hint">Keşfet&apos;te görmediği veride ayakta kalan stratejiler masaya eklenir. Masa her saat başı son kapanan barı tarar; roller kuralla karar verir, işlem yalnızca kağıt üstünde açılır. Emir gönderilmez.</p>
      {st && !st.status.persistent && (
        <p className="warn-text">Supabase bağlı değil: günlük sadece bellekte, sunucu yeniden başlayınca silinir.</p>
      )}
      {st && st.status.persistent && !st.status.schedule_secret_set && (
        <p className="warn-text">CRON_SECRET ayarlı değil; saatlik çalışma başlamaz.</p>
      )}
      {(st?.status.access_code_required ?? false) && (
        <label>Masa erişim kodu
          <input type="password" value={desk.code} autoComplete="off" onChange={(e) => desk.setCode(e.target.value)} />
        </label>
      )}
      <button type="button" disabled={!!desk.busy} onClick={desk.run}>
        {desk.busy === "run" ? "Masa çalışıyor…" : "Masayı şimdi çalıştır"}
      </button>
      {desk.notice && <p className="hint ok-text">{desk.notice}</p>}
      {desk.error && <p className="error">{desk.error}</p>}

      {s && (
        <>
          <h2>Kağıt hesap</h2>
          <div className="row">
            <label>Başlangıç<input type="number" min={1} value={s.initial_capital} onChange={num("initial_capital")} /></label>
            <label>İşlem başı risk %<input type="number" min={0.05} max={5} step={0.05} value={s.risk_per_trade_pct} onChange={num("risk_per_trade_pct")} /></label>
          </div>
          <div className="row">
            <label>Günlük limit %<input type="number" min={0.5} max={50} step={0.5} value={s.daily_loss_limit_pct} onChange={num("daily_loss_limit_pct")} /></label>
            <label>Toplam kayıp %<input type="number" min={1} max={90} step={1} value={s.max_total_loss_pct} onChange={num("max_total_loss_pct")} /></label>
          </div>
          <div className="row">
            <label>Maks. açık pozisyon<input type="number" min={1} max={20} value={s.max_open_positions} onChange={num("max_open_positions")} /></label>
            <label>Veri
              <select value={s.data_source} onChange={(e) => setS({ ...s, data_source: e.target.value as DeskSettings["data_source"] })}>
                <option value="binance">Gerçek (kripto: Binance, US100/US500: Yahoo)</option>
                <option value="demo">Demo (sentetik)</option>
              </select>
            </label>
          </div>
          <h2>Tarayıcı</h2>
          <label>İzleme listesi (ör. BTC/USDT, US100)<input value={watch} onChange={(e) => setWatch(e.target.value.toUpperCase())} /></label>
          <div className="row">
            <label>Hacim eşiği (×)<input type="number" min={1.1} max={20} step={0.1} value={s.volume_spike_x} onChange={num("volume_spike_x")} /></label>
            <label>Ortalama (gün)<input type="number" min={3} max={90} value={s.volume_lookback_days} onChange={num("volume_lookback_days")} /></label>
          </div>
          <button type="button" className="ghost" disabled={!!desk.busy}
            onClick={() => desk.saveSettings({ ...s, watchlist: watch.split(/[,\s]+/).filter(Boolean) })}>
            {desk.busy === "settings" ? "Kaydediliyor…" : "Ayarları kaydet"}
          </button>
          <p className="hint">Risk, tüm açık stopların birlikte çalıştığı en kötü durumu hesaplar; bu durum günlük ya da toplam sınırı aşacaksa yeni işleme RED verir.</p>
        </>
      )}
    </>
  );
}

export function DeskView({ desk }: { desk: Desk }) {
  const st = desk.state;
  const [feedAll, setFeedAll] = useState(false);
  if (!st) {
    return <div className="panel empty"><h2>Masa</h2><p className="muted">{desk.error ?? "Yükleniyor…"}</p></div>;
  }
  const snap = st.snapshot;
  const a = snap?.account;
  const names = Object.fromEntries(st.strategies.map((s) => [s.id, s.name]));
  const r = desk.report;

  return (
    <>
      {a?.halted && (
        <div className="banner warn">
          {a.halted === "total" ? "Toplam zarar sınırı aşıldı — masa yeni işlem açmıyor." : "Günlük zarar sınırına ulaşıldı — masa yarına kadar yeni işlem açmıyor."}
        </div>
      )}
      <div className="metrics">
        <Metric label="Kağıt bakiye" value={a ? fmt(a.equity) : "—"} cls={tone(a?.total_pnl_pct)} sub={a ? `toplam ${pct(a.total_pnl_pct)} · başlangıç ${fmt(a.initial, 0)}` : "henüz çalışmadı"} />
        <Metric label="Bugün" value={a ? pct(a.day_pnl_pct) : "—"} cls={tone(a?.day_pnl_pct)} sub={a ? `günlük taban ${fmt(a.day_floor)}` : ""} />
        <Metric label="Açık risk" value={a ? fmt(a.open_risk) : "—"} sub={`${snap?.positions.length ?? 0} pozisyon · açık K/Z ${a ? fmt(a.open_pnl) : "—"}`} />
        <Metric label="Son çalışma" value={snap ? shortTime(snap.at).slice(11) : "—"} sub={snap ? `${shortTime(snap.at).slice(0, 10)} · ${snap.trigger === "schedule" ? "zamanlayıcı" : "elle"}` : "masayı çalıştır"} />
      </div>
      {snap && snap.errors.length > 0 && (
        <div className="banner warn">Son çalışmada {snap.errors.length} hata: {snap.errors.map((e) => `${e.name ?? e.symbol}: ${e.error}`).join(" · ")}</div>
      )}

      <div className="panel">
        <div className="panel-head"><h2>Masadaki stratejiler</h2><span className="muted">canlı = eklendiği andan beri her sinyal (gölge kayıt)</span></div>
        {st.strategies.length === 0 ? (
          <p className="muted">Masada strateji yok. Keşfet sekmesinde bir arama yap; &quot;Sağlam&quot; ya da &quot;Umut verici&quot; çıkan finalistlerin yanındaki &quot;Masaya ekle&quot; düğmesini kullan.</p>
        ) : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Strateji</th><th>Piyasa</th><th>Keşif</th><th className="r">Holdout PF</th><th className="r">Canlı işlem</th><th className="r">Canlı PF</th><th className="r">Canlı ort. R</th><th className="r">Masa işlem</th><th>Eklendi</th><th></th></tr></thead>
              <tbody>
                {st.strategies.map((s) => (
                  <tr key={s.id} className={s.active ? "" : "disabled"}>
                    <td>{s.name}</td>
                    <td>{s.symbol} {s.timeframe}</td>
                    <td><span className={`tag sm ${s.verdict === "robust" ? "good" : "warn"}`}>{s.verdict === "robust" ? "sağlam" : "umut verici"}</span></td>
                    <td className="r">{fmt(s.discovery.holdout?.profit_factor)}</td>
                    <td className="r">{s.forward.trades}</td>
                    <td className={`r ${s.forward.profit_factor != null ? tone(s.forward.profit_factor - 1) : ""}`}>{fmt(s.forward.profit_factor)}</td>
                    <td className={`r ${tone(s.forward.avg_r)}`}>{fmt(s.forward.avg_r)}</td>
                    <td className="r">{s.desk_stats.trades}</td>
                    <td className="muted">{shortTime(s.activated_at).slice(0, 10)}</td>
                    <td><button type="button" className="link" disabled={!!desk.busy} onClick={() => desk.setActive(s.id, !s.active)}>{s.active ? "durdur" : "yeniden başlat"}</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {snap && snap.positions.length > 0 && (
        <div className="panel">
          <div className="panel-head"><h2>Açık pozisyonlar</h2><span className="muted">kağıt</span></div>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Strateji</th><th>Piyasa</th><th>Durum</th><th>Giriş</th><th className="r">Giriş fiyatı</th><th className="r">Stop</th><th className="r">Hedef</th><th className="r">Son</th><th className="r">R</th><th className="r">K/Z</th></tr></thead>
              <tbody>
                {snap.positions.map((p) => (
                  <tr key={`${p.strategy_id}-${p.entry_time ?? p.signal_time}`}>
                    <td>{p.name}</td>
                    <td>{p.symbol} {p.timeframe}</td>
                    <td>{p.status === "open" ? <span className="tag sm good">açık</span> : <span className="tag sm">sonraki açılışta</span>}</td>
                    <td>{p.entry_time ? shortTime(p.entry_time) : "—"}</td>
                    <td className="r">{fmt(p.entry_price, 4)}</td>
                    <td className="r">{fmt(p.stop_price, 4)}</td>
                    <td className="r">{fmt(p.target_price, 4)}</td>
                    <td className="r">{fmt(p.last_price, 4)}</td>
                    <td className={`r ${tone(p.r_multiple)}`}>{fmt(p.r_multiple)}</td>
                    <td className={`r ${tone(p.pnl)}`}>{fmt(p.pnl)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      <div className="panel">
        <div className="panel-head"><h2>Masa konuşması</h2><span className="muted">her sinyalde rollerin söyledikleri · en yenisi üstte</span></div>
        {st.decisions.length === 0 ? (
          <p className="muted">Henüz karar yok. Masa sadece masadaki stratejilerin son kapanan barda verdiği yeni giriş sinyallerinde konuşur; sinyal seyrek olabilir.</p>
        ) : (
          <div className="feed">{st.decisions.slice(0, feedAll ? 60 : 8).map((d) => <DecisionCard key={d.id} d={d} />)}</div>
        )}
        {st.decisions.length > 8 && (
          <button type="button" className="ghost" onClick={() => setFeedAll((v) => !v)}>
            {feedAll ? "Daha az göster" : `Son ${st.decisions.length} kararın tümünü göster`}
          </button>
        )}
      </div>

      {r && (
        <div className="panel">
          <div className="panel-head"><h2>Günlükçü · son 7 gün</h2><span className="muted">{r.runs}/{r.expected_runs} saatlik çalışma</span></div>
          <div className="checks">
            <Stat label="Kararlar" value={`${r.decisions.total}`} sub={`AL ${r.decisions.AL} · BEKLE ${r.decisions.BEKLE} · GEÇ ${r.decisions["GEÇ"]}`} />
            <Stat label="Kapanan masa işlemi" value={`${r.desk.trades}`} sub={r.desk.trades ? `isabet %${fmt(r.desk.win_rate_pct, 0)} · ort. ${fmt(r.desk.avg_r)}R` : "—"} />
            <Stat label="Haftalık K/Z" value={fmt(r.desk.pnl)} sub={`bakiye ${fmt(r.desk.equity)}`} />
            <Stat label="Şüpheci denetimi" value={r.skeptic_audit.trades ? `${r.skeptic_audit.sum_r > 0 ? "+" : ""}${fmt(r.skeptic_audit.sum_r)}R` : "—"}
              sub={r.skeptic_audit.trades ? `durdurduğu ${r.skeptic_audit.trades} işlem olsaydı` : "durdurulan işlemin sonucu henüz yok"} />
          </div>
          {r.suggestions.length > 0 && (
            <ul className="suggestions">{r.suggestions.map((s) => <li key={s}>{s}</li>)}</ul>
          )}
        </div>
      )}

      {st.trades.length > 0 && (
        <div className="panel">
          <div className="panel-head"><h2>Kapanan masa işlemleri</h2></div>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Strateji</th><th>Giriş</th><th>Çıkış</th><th className="r">Giriş fiyatı</th><th className="r">Çıkış fiyatı</th><th>Neden</th><th className="r">R</th></tr></thead>
              <tbody>
                {st.trades.map((t) => (
                  <tr key={`${t.strategy_id}-${t.entry_time}`}>
                    <td>{names[t.strategy_id] ?? "—"}</td>
                    <td>{shortTime(t.entry_time)}</td>
                    <td>{shortTime(t.exit_time)}</td>
                    <td className="r">{fmt(t.entry_price, 4)}</td>
                    <td className="r">{fmt(t.exit_price, 4)}</td>
                    <td className="muted">{EXIT_TR[t.exit_reason] ?? t.exit_reason}</td>
                    <td className={`r ${tone(t.r_multiple)}`}>{fmt(t.r_multiple)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {st.volume_spikes.length > 0 && (
        <div className="panel">
          <div className="panel-head"><h2>Tarayıcı · hacim sıçramaları</h2><span className="muted">bilgi amaçlı, işlem açmaz</span></div>
          <ul className="suggestions">{st.volume_spikes.map((v) => <li key={`${v.symbol}-${v.bar_time}`}><span className="muted">{shortTime(v.bar_time)}</span> {v.text.replace(/^Tarayıcı: /, "")}</li>)}</ul>
        </div>
      )}
      <p className="disclaimer">Masa kağıt üstünde çalışır, borsaya emir göndermez. İleriye dönük sonuçlar az işlemle yanıltıcı olabilir; bir stratejiyi değerlendirmeden önce en az 20–30 kapanmış işlem bekle.</p>
    </>
  );
}

function DecisionCard({ d }: { d: DeskDecision }) {
  return (
    <div className="decision">
      <div className="decision-head">
        <span className={`tag ${VERDICT_CLS[d.verdict]}`}>{d.verdict}</span>
        <b>{d.strategy_name}</b>
        <span className="muted">{d.symbol} {d.timeframe} · sinyal {shortTime(d.signal_time)}</span>
      </div>
      <ul>
        {d.roles.map((r) => (
          <li key={r.role}>
            <span className="role">{r.role}{r.status === "RED" && <b className="neg"> · RED</b>}</span>
            <span>{r.text}</span>
          </li>
        ))}
      </ul>
    </div>
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

function Stat({ label, value, sub }: { label: string; value: string; sub: string }) {
  return (
    <div className="check"><div><span className="label">{label}</span><b>{value}</b><span className="sub">{sub}</span></div></div>
  );
}
