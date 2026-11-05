"use client";

import type { RiskInfo, RiskSettings } from "@/lib/types";
import { fmt, pct } from "@/lib/format";

export const RISK_DEFAULTS: RiskSettings = {
  risk_per_trade_pct: 0.5,
  daily_loss_limit_pct: 3,
  max_total_loss_pct: 10,
  stop_type: "atr",
  stop_atr_period: 14,
  stop_atr_mult: 2,
  stop_pct: 2,
  take_profit_rr: 2,
};

export function RiskFields({ risk, onChange }: {
  risk: RiskSettings | null;
  onChange: (r: RiskSettings | null) => void;
}) {
  const r = risk ?? RISK_DEFAULTS;
  const set = <K extends keyof RiskSettings>(k: K, v: RiskSettings[K]) => onChange({ ...r, [k]: v });
  const num = (k: keyof RiskSettings, allowOff = false) => (e: React.ChangeEvent<HTMLInputElement>) => {
    const v = Number(e.target.value);
    set(k, (allowOff && v <= 0 ? null : v) as never);
  };
  return (
    <>
      <label className="toggle">
        <input type="checkbox" checked={!!risk} onChange={(e) => onChange(e.target.checked ? r : null)} />
        Risk kurallarını uygula
      </label>
      {risk && (
        <>
          <div className="row">
            <label>İşlem başı risk %<input type="number" min={0.05} max={10} step={0.05} value={r.risk_per_trade_pct} onChange={num("risk_per_trade_pct")} /></label>
            <label>Günlük limit %<input type="number" min={0} max={50} step={0.5} value={r.daily_loss_limit_pct ?? 0} onChange={num("daily_loss_limit_pct", true)} /></label>
          </div>
          <div className="row">
            <label>Toplam kayıp %<input type="number" min={0} max={90} step={1} value={r.max_total_loss_pct ?? 0} onChange={num("max_total_loss_pct", true)} /></label>
            <label>Hedef (R:R)<input type="number" min={0} max={20} step={0.5} value={r.take_profit_rr ?? 0} onChange={num("take_profit_rr", true)} /></label>
          </div>
          <div className="row">
            <label>Stop tipi
              <select value={r.stop_type} onChange={(e) => set("stop_type", e.target.value as RiskSettings["stop_type"])}>
                <option value="atr">ATR katı</option>
                <option value="pct">Sabit %</option>
              </select>
            </label>
            {r.stop_type === "atr" ? (
              <label>ATR × <input type="number" min={0.25} max={20} step={0.25} value={r.stop_atr_mult} onChange={num("stop_atr_mult")} /></label>
            ) : (
              <label>Stop %<input type="number" min={0.1} max={50} step={0.1} value={r.stop_pct} onChange={num("stop_pct")} /></label>
            )}
          </div>
          <p className="hint">Pozisyon büyüklüğü = bakiye × risk ÷ stop mesafesi (en fazla 1× bakiye). Stop ve hedef bar içinde kontrol edilir. 0 = kapalı. Kendi stratejinde stop yazdıysan o kullanılır.</p>
        </>
      )}
    </>
  );
}

export function RiskReport({ info }: { info: RiskInfo }) {
  const c = info.config;
  const dayOk = c.daily_loss_limit_pct == null || info.daily_limit_breaches === 0;
  const reasons: Record<string, string> = {
    stop: "stop", target: "hedef", signal: "sinyal", daily_limit: "günlük limit",
    max_total_loss: "toplam limit", end_of_data: "veri sonu",
  };
  return (
    <div className="panel">
      <div className="panel-head">
        <h2>Risk kuralları kontrolü</h2>
        <span className={`tag ${info.account_failed ? "bad" : dayOk ? "good" : "warn"}`}>
          {info.account_failed ? "Hesap patladı" : dayOk ? "Kurallara uydu" : "Günlük limit ihlali"}
        </span>
      </div>
      <div className="checks">
        <Check ok={c.daily_loss_limit_pct == null || info.worst_day_pct > -c.daily_loss_limit_pct}
          label="En kötü gün" value={`${pct(info.worst_day_pct)}${info.worst_day ? ` (${info.worst_day})` : ""}`}
          limit={c.daily_loss_limit_pct != null ? `limit −%${c.daily_loss_limit_pct}` : "limit yok"} />
        <Check ok={!info.account_failed} label="En düşük bakiye"
          value={`başlangıcın %${fmt(info.lowest_equity_pct_of_initial, 1)}'i`}
          limit={c.max_total_loss_pct != null ? `limit %${100 - c.max_total_loss_pct}` : "limit yok"} />
        <Check ok={info.daily_limit_breaches === 0} label="Günlük limit ihlali" value={String(info.daily_limit_breaches)} limit={`${info.trading_days} gün`} />
        <Check ok={info.avg_monthly_return_pct > 0} label="Ortalama aylık getiri" value={pct(info.avg_monthly_return_pct)} limit={`pozitif ay %${fmt(info.positive_months_pct, 0)}`} />
      </div>
      <p className="hint">
        Ortalama R: {fmt(info.avg_r_multiple)} · ortalama pozisyon: bakiyenin %{fmt(info.avg_position_pct, 0)}'i · çıkışlar:{" "}
        {Object.entries(info.exit_reasons).filter(([, n]) => n > 0).map(([k, n]) => `${reasons[k] ?? k} ${n}`).join(", ") || "—"}
        {info.failed_at && <> · patlama: {info.failed_at.slice(0, 16).replace("T", " ")}</>}
      </p>
    </div>
  );
}

function Check({ ok, label, value, limit }: { ok: boolean; label: string; value: string; limit: string }) {
  return (
    <div className={`check ${ok ? "ok" : "fail"}`}>
      <span className="dot">{ok ? "✓" : "✗"}</span>
      <div><span className="label">{label}</span><b>{value}</b><span className="sub">{limit}</span></div>
    </div>
  );
}
