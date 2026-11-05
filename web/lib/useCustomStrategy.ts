"use client";

import { useCallback, useEffect, useState } from "react";
import { aiStatus, aiTranslate, validateSpec } from "./api";
import { load, save } from "./storage";
import type { RulesText, SavedStrategy, StrategyJSON } from "./types";

const SAVED_KEY = "strategy-lab:saved";
const CODE_KEY = "strategy-lab:ai-code";
const DRAFT_KEY = "strategy-lab:draft";

export function useCustomStrategy() {
  const [text, setText] = useState("");
  const [spec, setSpec] = useState<StrategyJSON | null>(null);
  const [rules, setRules] = useState<RulesText | null>(null);
  const [explanation, setExplanation] = useState("");
  const [unsupported, setUnsupported] = useState<string[]>([]);
  const [json, setJson] = useState("");
  const [busy, setBusy] = useState<"ai" | "validate" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<{ enabled: boolean; access_code_required: boolean } | null>(null);
  const [accessCode, setAccessCodeState] = useState("");
  const [saved, setSaved] = useState<SavedStrategy[]>([]);

  useEffect(() => {
    setSaved(load<SavedStrategy[]>(SAVED_KEY, []));
    setAccessCodeState(load<string>(CODE_KEY, ""));
    setText(load<string>(DRAFT_KEY, ""));
    aiStatus().then(setStatus).catch(() => setStatus({ enabled: false, access_code_required: false }));
  }, []);

  const updateText = (v: string) => { setText(v); save(DRAFT_KEY, v); };
  const setAccessCode = (v: string) => { setAccessCodeState(v); save(CODE_KEY, v); };

  const apply = (next: StrategyJSON, nextRules: RulesText) => {
    setSpec(next);
    setRules(nextRules);
    setJson(JSON.stringify(next, null, 2));
  };

  const translate = useCallback(async () => {
    setBusy("ai");
    setError(null);
    try {
      const out = await aiTranslate(text, accessCode || undefined);
      apply(out.spec, out.rules);
      setExplanation(out.explanation);
      setUnsupported(out.unsupported);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }, [text, accessCode]);

  const validateJson = useCallback(async () => {
    setBusy("validate");
    setError(null);
    try {
      const parsed = JSON.parse(json);
      const out = await validateSpec(parsed);
      apply(out.spec, out.rules);
    } catch (e) {
      setError(e instanceof SyntaxError ? `JSON hatalı: ${e.message}` : e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }, [json]);

  const saveCurrent = () => {
    if (!spec) return;
    const name = String(spec.name || "Özel strateji");
    const item: SavedStrategy = { id: `${Date.now()}`, name, text, spec, savedAt: new Date().toISOString() };
    const next = [item, ...saved.filter((s) => s.name !== name)].slice(0, 30);
    setSaved(next);
    save(SAVED_KEY, next);
  };

  const remove = (id: string) => {
    const next = saved.filter((s) => s.id !== id);
    setSaved(next);
    save(SAVED_KEY, next);
  };

  const open = async (item: SavedStrategy) => {
    updateText(item.text);
    setExplanation("");
    setUnsupported([]);
    setError(null);
    try {
      const out = await validateSpec(item.spec);
      apply(out.spec, out.rules);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  return {
    text, setText: updateText, spec, rules, explanation, unsupported, json, setJson, busy, error,
    status, accessCode, setAccessCode, saved, translate, validateJson, saveCurrent, remove, open,
  };
}

export type CustomStrategyState = ReturnType<typeof useCustomStrategy>;
