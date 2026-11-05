"use client";

import { fmt, pct, shortTime, tone } from "@/lib/format";
import type { DiscoveryCandidate, DiscoveryResult, SegmentResult } from "@/lib/types";

export function DiscoveryFields({ n, seed, onN, onSeed }: {
  n: number; seed: number; onN: (v: number) => void; onSeed: (v: number) => void;
}) {
  return (
    <>
      <p className="hint">Sistem yüzlerce strateji dener; veriyi zamana göre üçe böler: %60 eğitim, %20 doğrulama, %20 hiç dokunulmamış son dönem. Sadece üçünde de risk kurallarına uyarak kazanan ve şans testini geçenler &quot;sağlam&quot; sayılır.</p>
      <div className="row">
        <label>Aday sayısı<input type="number" min={20} max={300} step={10} value={n} onChange={(e) => onN(Number(e.target.value))} /></label>
        <label>Seed<input type="number" min={0} value={seed} onChange={(e) => onSeed(Number(e.target.value))} /></label>
      </div>
      <p className="hint">Daha uzun geçmiş = daha güvenilir sonuç. 1h için 10.000–20.000 bar öner (≈ 14–28 ay). Aynı seed aynı adayları üretir.</p>
    </>
  );
}

const VERDICT_CLS: Record<string, string> = { robust: "good", promising: "warn", failed_holdout: "bad" };

export function DiscoveryReport({ d, onOpen }: { d: DiscoveryResult; onOpen: (c: DiscoveryCandidate) => void }) {
  const f = d.funnel;
  const seg = d.segments;
  return (
    <>
      <div className="panel">
        <div className="panel-head">
          <h2>Strateji keşfi</h2>
          <span className="muted">{d.symbol} {d.timeframe} · {f.evaluated} aday · {fmt(d.elapsed_s, 1)} sn · seed {d.seed}</span>
        </div>
        {d.synthetic_data && <p className="warn-text">Sentetik demo verisi — içinde gerçek bir avantaj yoktur; &quot;sağlam&quot; aday çıkmaması beklenir.</p>}
        <div className="funnel">
          <Step n={f.evaluated} label="denendi" />
          <Step n={f.passed_train} label="eğitimi geçti" />
          <Step n={f.validated} label="doğrulamayı geçti" />
          <Step n={f.finalists} label="son dönemde ölçüldü" />
          <Step n={f.robust} label="sağlam" strong />
        </div>
        <div className="segments">
          {(["train", "validation", "holdout"] as const).map((k) => (
            <div key={k} className="segment">
              <span className="label">{{ train: "Eğitim", validation: "Doğrulama", holdout: "Son dönem (holdout)" }[k]}</span>
              <b>{shortTime(seg[k].start).slice(0, 10)} → {shortTime(seg[k].end).slice(0, 10)}</b>
              <span className="sub">{seg[k].bars} bar · al-tut aylık {pct(seg[k].buy_and_hold_monthly_pct, 1)}</span>
            </div>
          ))}
        </div>
        <p className="hint">Etkin bağımsız deneme sayısı ≈ {fmt(d.thresholds.effective_trials, 1)} (birbirine benzeyen adaylar tek sayılır). Şans testi eşiği: %{fmt(d.thresholds.deflated_sharpe_min * 100, 0)}.</p>
      </div>

      {d.results.length === 0 ? (
        <div className="panel"><p className="muted">Hiçbir aday doğrulama aşamasını geçemedi. Bu dürüst bir sonuç: bu veride, bu risk kurallarıyla kalıcı bir avantaj bulunamadı. Başka bir sembol, zaman dilimi veya daha uzun geçmiş dene.</p></div>
      ) : (
        <div className="panel">
          <div className="panel-head"><h2>Finalistler</h2><span className="muted">dokununca kurallarıyla açılır</span></div>
          <div className="table-wrap">
            <table>
              <thead><tr><th>Strateji</th><th>Sonuç</th><th className="r">Eğitim/ay</th><th className="r">Doğrulama/ay</th><th className="r">Son dönem/ay</th><th className="r">İşlem</th><th className="r">PF</th><th className="r">En kötü gün</th><th className="r">Şans testi</th></tr></thead>
              <tbody>
                {d.results.map((c) => (
                  <tr key={c.id} className="click" onClick={() => onOpen(c)}>
                    <td>{c.name}</td>
                    <td><span className={`tag sm ${VERDICT_CLS[c.verdict]}`}>{c.verdict_label}</span></td>
                    <Cell s={c.train} />
                    <Cell s={c.validation} />
                    <Cell s={c.holdout} strong />
                    <td className="r">{c.holdout.trades}</td>
                    <td className="r">{fmt(c.holdout.profit_factor)}</td>
                    <td className="r neg">{pct(c.holdout.worst_day_pct, 2)}</td>
                    <td className={`r ${c.deflated_sharpe_prob >= d.thresholds.deflated_sharpe_min ? "pos" : "muted"}`}>%{fmt(c.deflated_sharpe_prob * 100, 0)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="hint">&quot;Son dönem&quot; seçimde hiç kullanılmadı; görmediği veride nasıl davrandığının en dürüst tahmini budur. Aylık değerler bileşik eşdeğerdir. Şans testi = Deflated Sharpe: bu kadar deneme yapılmışken gerçek Sharpe&apos;ın sıfırdan büyük olma olasılığı.</p>
        </div>
      )}
    </>
  );
}

function Step({ n, label, strong }: { n: number; label: string; strong?: boolean }) {
  return <div className={`step ${strong ? "strong" : ""}`}><b>{n}</b><span>{label}</span></div>;
}

function Cell({ s, strong }: { s: SegmentResult; strong?: boolean }) {
  return <td className={`r ${tone(s.monthly_pct)} ${strong ? "bold" : ""}`}>{pct(s.monthly_pct, 2)}</td>;
}
