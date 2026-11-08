"use client";

import { useCallback, useEffect, useState } from "react";
import { deskAdd, deskReport, deskRun, deskSaveSettings, deskSetActive, deskState } from "./api";
import { load, save } from "./storage";
import type { DeskReport, DeskSettings, DeskState, DiscoveryCandidate, DiscoveryResult } from "./types";

const CODE_KEY = "strategy-lab:desk-code";

export function useDesk(enabled: boolean) {
  const [state, setState] = useState<DeskState | null>(null);
  const [report, setReport] = useState<DeskReport | null>(null);
  const [code, setCodeRaw] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [added, setAdded] = useState<Record<string, boolean>>({});

  useEffect(() => setCodeRaw(load(CODE_KEY, "")), []);
  const setCode = (v: string) => { setCodeRaw(v); save(CODE_KEY, v); };

  const refresh = useCallback(async () => {
    try {
      const [s, r] = await Promise.all([deskState(), deskReport(7)]);
      setState(s);
      setReport(r);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    if (!enabled) return;
    refresh();
    const t = window.setInterval(refresh, 60_000);
    return () => window.clearInterval(t);
  }, [enabled, refresh]);

  async function act(label: string, fn: () => Promise<unknown>, ok?: (v: never) => string) {
    setBusy(label);
    setError(null);
    setNotice(null);
    try {
      const v = await fn();
      if (ok) setNotice(ok(v as never));
      await refresh();
      return true;
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      return false;
    } finally {
      setBusy(null);
    }
  }

  const run = () => act("run", () => deskRun(code || undefined),
    (r: { new_decisions: number; approved: number }) =>
      r.new_decisions ? `${r.new_decisions} yeni karar, ${r.approved} AL.` : "Yeni sinyal yok.");

  const setActive = (id: string, active: boolean) =>
    act(`toggle:${id}`, () => deskSetActive(id, active, code || undefined));

  const saveSettings = (s: DeskSettings) =>
    act("settings", () => deskSaveSettings(s, code || undefined), () => "Ayarlar kaydedildi.");

  async function addFromDiscovery(d: DiscoveryResult, c: DiscoveryCandidate) {
    const okAdd = await act(`add:${c.id}`, () => deskAdd({
      name: c.name, symbol: d.symbol, timeframe: d.timeframe, spec: c.spec,
      stop_atr_mult: c.risk.stop_atr_mult, take_profit_rr: c.risk.take_profit_rr,
      verdict: c.verdict,
      discovery: { holdout: c.holdout, validation: c.validation, train: c.train,
                   deflated_sharpe_prob: c.deflated_sharpe_prob, segments: d.segments,
                   data_source: d.data_source, seed: d.seed },
    }, code || undefined), () => `"${c.name}" masaya eklendi; ileriye dönük test şu andan başlar.`);
    if (okAdd) setAdded((a) => ({ ...a, [c.id]: true }));
  }

  return { state, report, code, setCode, busy, error, notice, added, refresh, run, setActive,
           saveSettings, addFromDiscovery };
}

export type Desk = ReturnType<typeof useDesk>;
