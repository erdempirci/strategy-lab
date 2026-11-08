"use client";

import { useState } from "react";
import { inspectCSV } from "@/lib/api";
import { shortTime } from "@/lib/format";
import type { CSVInfo, MarketParams } from "@/lib/types";

type Preset = { id: string; label: string; source: MarketParams["data_source"]; symbol: string;
                timeframe: string; fee_bps: number; slippage_bps: number };

export const PRESETS: { group: string; items: Preset[] }[] = [
  { group: "Altın ve endeksler (Yahoo vadeli · 1h–1d)", items: [
    { id: "xau", label: "Altın · XAUUSD (GC vadeli)", source: "yahoo", symbol: "XAUUSD", timeframe: "1h", fee_bps: 0.5, slippage_bps: 1 },
    { id: "nq", label: "Nasdaq 100 · US100 (NQ vadeli)", source: "yahoo", symbol: "US100", timeframe: "1h", fee_bps: 0.5, slippage_bps: 0.5 },
    { id: "es", label: "S&P 500 · US500 (ES vadeli)", source: "yahoo", symbol: "US500", timeframe: "1h", fee_bps: 0.5, slippage_bps: 0.5 },
  ] },
  { group: "Kripto (Binance · 15m–1d)", items: [
    { id: "btc", label: "BTC/USDT", source: "binance", symbol: "BTC/USDT", timeframe: "1h", fee_bps: 10, slippage_bps: 2 },
    { id: "eth", label: "ETH/USDT", source: "binance", symbol: "ETH/USDT", timeframe: "1h", fee_bps: 10, slippage_bps: 2 },
    { id: "sol", label: "SOL/USDT", source: "binance", symbol: "SOL/USDT", timeframe: "1h", fee_bps: 10, slippage_bps: 3 },
    { id: "bnb", label: "BNB/USDT", source: "binance", symbol: "BNB/USDT", timeframe: "1h", fee_bps: 10, slippage_bps: 3 },
    { id: "xrp", label: "XRP/USDT", source: "binance", symbol: "XRP/USDT", timeframe: "1h", fee_bps: 10, slippage_bps: 3 },
    { id: "paxg", label: "PAXG/USDT · altın token (7/24)", source: "binance", symbol: "PAXG/USDT", timeframe: "1h", fee_bps: 10, slippage_bps: 5 },
  ] },
  { group: "Hisse / ETF (Yahoo · sadece günlük)", items: [
    { id: "spy", label: "SPY · S&P 500 ETF", source: "yahoo", symbol: "SPY", timeframe: "1d", fee_bps: 1, slippage_bps: 1 },
    { id: "qqq", label: "QQQ · Nasdaq 100 ETF", source: "yahoo", symbol: "QQQ", timeframe: "1d", fee_bps: 1, slippage_bps: 1 },
    { id: "gld", label: "GLD · Altın ETF", source: "yahoo", symbol: "GLD", timeframe: "1d", fee_bps: 1, slippage_bps: 1 },
  ] },
];

const FUTURES = new Set(["US100", "US500", "XAUUSD", "NAS100", "NQ", "NDX", "USTEC", "SPX500", "ES",
                         "SPX", "XAU", "GOLD", "ALTIN", "GC"]);
const CRYPTO_TF = ["15m", "30m", "1h", "4h", "1d"];
const FUTURES_TF = ["1h", "2h", "4h", "1d"];

export function timeframesFor(m: MarketParams, csv: CSVInfo | null): string[] {
  if (m.data_source === "csv") return csv ? [csv.timeframe] : [m.timeframe];
  if (m.data_source === "yahoo") return FUTURES.has(m.symbol.toUpperCase()) ? FUTURES_TF : ["1d"];
  return CRYPTO_TF;
}

/** TradingView names exports like "OANDA_XAUUSD, 60.csv" → "XAUUSD". */
function symbolFromFilename(name: string): string {
  const base = name.replace(/\.csv$/i, "").split(",")[0].trim();
  const sym = base.includes("_") ? base.slice(base.indexOf("_") + 1) : base;
  return (sym || "CSV").toUpperCase().slice(0, 30).padEnd(3, "_");
}

export function MarketPicker({ market, setMarket, csv, setCsv, maxBars }: {
  market: MarketParams;
  setMarket: (fn: (m: MarketParams) => MarketParams) => void;
  csv: CSVInfo | null;
  setCsv: (c: CSVInfo | null) => void;
  maxBars: number;
}) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const presetId = PRESETS.flatMap((g) => g.items)
    .find((p) => p.source === market.data_source && p.symbol === market.symbol)?.id ?? "";

  const choosePreset = (id: string) => {
    const p = PRESETS.flatMap((g) => g.items).find((x) => x.id === id);
    if (!p) return;
    setCsv(null);
    setMarket((m) => ({ ...m, data_source: p.source, symbol: p.symbol, csv_data: null,
                        timeframe: p.timeframe, fee_bps: p.fee_bps, slippage_bps: p.slippage_bps }));
  };

  const chooseSource = (src: MarketParams["data_source"]) => {
    if (src !== "csv") setCsv(null);
    setMarket((m) => {
      const next = { ...m, data_source: src, csv_data: src === "csv" ? m.csv_data : null };
      if (src === "binance" && !m.symbol.includes("/")) next.symbol = "BTC/USDT";
      if (src === "yahoo" && m.symbol.includes("/")) next.symbol = "XAUUSD";
      const tfs = timeframesFor(next, null);
      if (src !== "csv" && !tfs.includes(next.timeframe)) next.timeframe = tfs[0];
      return next;
    });
  };

  async function onFile(file: File | undefined) {
    if (!file) return;
    setErr(null);
    setBusy(true);
    try {
      if (file.size > 7_500_000) throw new Error("Dosya en fazla ~7 MB olabilir (≈100.000 bar)");
      const text = await file.text();
      const info = await inspectCSV(text);
      setCsv(info);
      setMarket((m) => ({ ...m, data_source: "csv", csv_data: text, timeframe: info.timeframe,
                          symbol: symbolFromFilename(file.name),
                          bars: Math.min(info.bars, maxBars) }));
    } catch (e) {
      setCsv(null);
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <label>Hazır piyasa
        <select value={presetId} onChange={(e) => choosePreset(e.target.value)}>
          <option value="" disabled>— seç —</option>
          {PRESETS.map((g) => (
            <optgroup key={g.group} label={g.group}>
              {g.items.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
            </optgroup>
          ))}
        </select>
      </label>
      <label>Veri kaynağı
        <select value={market.data_source} onChange={(e) => chooseSource(e.target.value as MarketParams["data_source"])}>
          <option value="binance">Kripto · Binance</option>
          <option value="yahoo">Yahoo · altın, endeks, hisse</option>
          <option value="csv">TradingView / CSV yükle</option>
          <option value="demo">Demo (sentetik)</option>
        </select>
      </label>

      {market.data_source === "csv" ? (
        <>
          <label>CSV dosyası
            <input type="file" accept=".csv,.txt,text/csv" disabled={busy}
              onChange={(e) => onFile(e.target.files?.[0])} />
          </label>
          {busy && <p className="hint">Dosya okunuyor…</p>}
          {err && <p className="error">{err}</p>}
          {csv ? (
            <p className="hint">
              <b className="plain">{market.symbol}</b> · {csv.bars.toLocaleString("tr-TR")} bar · {csv.timeframe} ·{" "}
              {shortTime(csv.start).slice(0, 10)} → {shortTime(csv.end).slice(0, 10)}
              {csv.quality.gaps > 0 && <> · {csv.quality.missing_bars_estimate} eksik bar</>}
            </p>
          ) : (
            <p className="hint">TradingView: grafiği aç → sağ üstteki menü (veya grafik adının yanındaki ok) → <b>Export chart data…</b> → zaman biçimi ISO veya UNIX → indir. Dosya, grafikte yüklü olan barları içerir; daha fazla bar için grafiği geriye kaydırıp sonra dışa aktar.</p>
          )}
        </>
      ) : (
        <label>Sembol
          <input value={market.symbol} list={`symbols-${market.data_source}`}
            onChange={(e) => {
              const v = e.target.value.toUpperCase();
              setMarket((m) => {
                const next = { ...m, symbol: v };
                const tfs = timeframesFor(next, null);
                if (!tfs.includes(next.timeframe)) next.timeframe = tfs[0];
                return next;
              });
            }} />
          <datalist id="symbols-binance">
            {["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT", "PAXG/USDT", "DOGE/USDT", "ADA/USDT", "AVAX/USDT", "LINK/USDT"].map((s) => <option key={s} value={s} />)}
          </datalist>
          <datalist id="symbols-yahoo">
            {["XAUUSD", "US100", "US500", "SPY", "QQQ", "GLD", "DIA", "IWM", "AAPL", "NVDA", "MSFT"].map((s) => <option key={s} value={s} />)}
          </datalist>
          <datalist id="symbols-demo"><option value="BTC/USDT" /></datalist>
        </label>
      )}
      {market.data_source === "yahoo" && (
        <p className="hint">XAUUSD / US100 / US500 = vadeli kontratlar (haftada 5 gün, ~23 saat; ~10 dk gecikmeli; 1h geçmişi ~2 yıl ≈ 11.000 bar, günlükte onlarca yıl). Diğer semboller hisse/ETF olarak günlük ve temettü düzeltmeli gelir.</p>
      )}
    </>
  );
}
