"use client";

import type { CustomStrategyState } from "@/lib/useCustomStrategy";

const EXAMPLES = [
  "Fiyat EMA200'ün üstündeyken RSI(14) 30'u yukarı keserse al. RSI 70'e gelince sat. %2 stop, %6 hedef.",
  "Son 20 barın en yükseği kırılınca al, son 10 barın en düşüğünün altında kapanış olursa çık.",
  "Bollinger(20,2) alt bandının altında kapanıştan sonra fiyat tekrar alt bandın üstüne çıkarsa al, orta bantta sat.",
  "Sadece Londra seansında (İstanbul saatiyle 10:00-19:00) MACD histogramı pozitife dönünce al, 12 bar sonra çık.",
];

export function CustomEditor({ c }: { c: CustomStrategyState }) {
  const aiOff = c.status !== null && !c.status.enabled;
  return (
    <>
      <label>Stratejini yaz
        <textarea rows={6} value={c.text} maxLength={3000}
          placeholder="Örn: Fiyat EMA200 üstündeyken RSI 30'u yukarı keserse al, RSI 70'te sat, %2 stop."
          onChange={(e) => c.setText(e.target.value)} />
      </label>
      <div className="chips">
        {EXAMPLES.map((ex, i) => (
          <button key={ex} type="button" className="chip" onClick={() => c.setText(ex)}>Örnek {i + 1}</button>
        ))}
      </div>
      {c.status?.access_code_required && (
        <label>AI erişim kodu
          <input type="password" value={c.accessCode} onChange={(e) => c.setAccessCode(e.target.value)} />
        </label>
      )}
      <button type="button" className="ghost" disabled={!!c.busy || c.text.trim().length < 3 || aiOff}
        onClick={c.translate}>
        {c.busy === "ai" ? "AI yorumluyor…" : `AI ile kurala çevir${c.status?.provider_label ? ` (${c.status.provider_label})` : ""}`}
      </button>
      {aiOff && <p className="hint warn-text">AI kapalı: Vercel'de GEMINI_API_KEY (ücretsiz), NVIDIA_API_KEY (ücretsiz) veya ANTHROPIC_API_KEY tanımlı değil. O zamana kadar JSON kurallarını elle yazıp test edebilirsin.</p>}
      {c.error && <p className="error">{c.error}</p>}

      {c.saved.length > 0 && (
        <>
          <h2>Kayıtlı stratejilerim</h2>
          <ul className="saved">
            {c.saved.map((s) => (
              <li key={s.id}>
                <button type="button" className="link" onClick={() => c.open(s)}>{s.name}</button>
                <button type="button" className="x" aria-label="Sil" onClick={() => c.remove(s.id)}>×</button>
              </li>
            ))}
          </ul>
        </>
      )}
    </>
  );
}

export function RulesCard({ c }: { c: CustomStrategyState }) {
  const r = c.rules;
  return (
    <div className="panel rules">
      <div className="panel-head">
        <h2>{c.spec ? String(c.spec.name ?? "Özel strateji") : "Özel strateji"}</h2>
        {c.spec && <button type="button" className="link" onClick={c.saveCurrent}>Kaydet</button>}
      </div>

      {!r && (
        <p className="muted">Stratejini sol tarafa düz Türkçe yaz ve &quot;AI ile kurala çevir&quot;e bas. AI'ın nasıl anladığını burada kural kural göreceksin; doğruysa backtest çalıştır.</p>
      )}

      {c.explanation && <p className="explain">{c.explanation}</p>}
      {c.unsupported.length > 0 && (
        <div className="banner warn">
          <b>Birebir uygulanamayan kısımlar:</b>
          <ul>{c.unsupported.map((u) => <li key={u}>{u}</li>)}</ul>
        </div>
      )}

      {r && (
        <div className="rule-grid">
          {r.indicators.length > 0 && <RuleBlock title="Göstergeler" lines={r.indicators} />}
          <RuleBlock title="Giriş (hepsi)" lines={r.entry} />
          {r.exit.length > 0 && <RuleBlock title="Çıkış" lines={r.exit} />}
          {r.risk.length > 0 && <RuleBlock title="Risk" lines={r.risk} />}
        </div>
      )}

      <details className="json" open={!r && c.status !== null && !c.status.enabled}>
        <summary>Kuralları JSON olarak düzenle</summary>
        <textarea rows={14} spellCheck={false} value={c.json} onChange={(e) => c.setJson(e.target.value)}
          placeholder={'{\n  "name": "Benim stratejim",\n  "indicators": [{"id": "ema200", "type": "ema", "period": 200}],\n  "entry": {"all": [{"left": "close", "op": ">", "right": "ema200"}]},\n  "exit": {"all": [{"left": "close", "op": "<", "right": "ema200"}]}\n}'} />
        <button type="button" className="ghost" disabled={!!c.busy || !c.json.trim()} onClick={c.validateJson}>
          {c.busy === "validate" ? "Doğrulanıyor…" : "JSON'u doğrula ve uygula"}
        </button>
      </details>
      <p className="hint">Stop ve hedef kapanış bazlıdır (bar içi değil): mum kapanışı seviyeyi geçerse bir sonraki açılışta çıkılır.</p>
    </div>
  );
}

function RuleBlock({ title, lines }: { title: string; lines: string[] }) {
  return (
    <div className="rule-block">
      <span className="label">{title}</span>
      <ul>{lines.map((l) => <li key={l}>{l}</li>)}</ul>
    </div>
  );
}
