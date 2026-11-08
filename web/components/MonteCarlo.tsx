"use client";

import type { MonteCarlo } from "@/lib/types";
import { fmt, pct } from "@/lib/format";

const LEVEL_TR = { good: "Sağlam", warn: "Belirsiz", bad: "Riskli" } as const;

export function MonteCarloPanel({ mc }: { mc: MonteCarlo }) {
  if (!mc.enough) {
    return (
      <div className="panel">
        <div className="panel-head"><h2>Monte Carlo (zaman makinesi)</h2><span className="tag">yetersiz veri</span></div>
        <p className="hint">Sadece {mc.trades} işlem var; Monte Carlo için en az {mc.min_trades} işlem gerekir. Daha fazla bar veya daha kısa zaman dilimi dene.</p>
      </div>
    );
  }
  const max = Math.max(...mc.histogram.counts, 1);
  const edges = mc.histogram.edges;
  return (
    <div className="panel">
      <div className="panel-head">
        <h2>Monte Carlo (zaman makinesi)</h2>
        <span className={`tag ${mc.level}`}>{LEVEL_TR[mc.level]}</span>
      </div>
      <p className="hint" style={{ margin: "0 0 12px", color: "var(--text)" }}>{mc.verdict}</p>
      <div className="checks">
        <Item ok={mc.prob_edge_negative_pct < 5} label="Şans olma ihtimali"
          value={`%${fmt(mc.prob_edge_negative_pct, 1)}`} sub="ortalama işlemin ≤ 0 çıktığı koşular" />
        <Item ok={mc.final_return_pct.p5 > 0} label="Getiri (kötü %5 / medyan)"
          value={`${pct(mc.final_return_pct.p5, 1)} / ${pct(mc.final_return_pct.p50, 1)}`}
          sub={`iyi %5: ${pct(mc.final_return_pct.p95, 1)} · zarar ihtimali %${fmt(mc.prob_loss_pct, 1)}`} />
        <Item ok={mc.max_total_loss_pct == null || mc.max_drawdown_pct.p95 > -mc.max_total_loss_pct}
          label="Max drawdown (medyan / kötü %5)"
          value={`${pct(mc.max_drawdown_pct.p50, 1)} / ${pct(mc.max_drawdown_pct.p95, 1)}`}
          sub={`kötü %1: ${pct(mc.max_drawdown_pct.p99, 1)}`} />
        <Item ok={mc.prob_total_loss_breach_pct == null || mc.prob_total_loss_breach_pct < 5}
          label="Toplam limit aşımı"
          value={mc.prob_total_loss_breach_pct == null ? "—" : `%${fmt(mc.prob_total_loss_breach_pct, 1)}`}
          sub={mc.max_total_loss_pct == null ? "limit yok" : `limit −%${mc.max_total_loss_pct}`} />
        <Item ok label="Win rate aralığı"
          value={`%${fmt(mc.win_rate_pct.p5, 0)} – %${fmt(mc.win_rate_pct.p95, 0)}`}
          sub={`medyan %${fmt(mc.win_rate_pct.p50, 1)}`} />
        <Item ok label="Art arda zarar"
          value={`${mc.losing_streak.p50} / ${mc.losing_streak.p95}`} sub="medyan / kötü %5 seri" />
      </div>
      <div className="mc-hist" aria-label="Son getiri dağılımı">
        {mc.histogram.counts.map((c, i) => (
          <i key={i} className={edges[i + 1] <= 0 ? "neg" : ""} style={{ height: `${(c / max) * 100}%` }}
            title={`${pct(edges[i], 1)} … ${pct(edges[i + 1], 1)}: ${c} koşu`} />
        ))}
      </div>
      <div className="mc-axis"><span>{pct(edges[0], 0)}</span><span>son getiri dağılımı</span><span>{pct(edges[edges.length - 1], 0)}</span></div>
      <p className="hint" style={{ marginTop: 10 }}>
        {mc.runs.toLocaleString("tr-TR")} koşu · her biri {mc.trades} işlem, stratejinin kendi işlemlerinden rastgele (iadeli) seçilip birleştirildi · seed {mc.seed}. {mc.limitations}
      </p>
    </div>
  );
}

function Item({ ok, label, value, sub }: { ok: boolean; label: string; value: string; sub: string }) {
  return (
    <div className={`check ${ok ? "ok" : "fail"}`}>
      <span className="dot">{ok ? "✓" : "✗"}</span>
      <div><span className="label">{label}</span><b>{value}</b><span className="sub">{sub}</span></div>
    </div>
  );
}
