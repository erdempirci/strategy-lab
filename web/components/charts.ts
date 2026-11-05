import { ColorType, type DeepPartial, type ChartOptions } from "lightweight-charts";

export const COLORS = {
  up: "#2fbf8f",
  down: "#e5566b",
  fast: "#f2b84b",
  slow: "#6aa8ff",
  equity: "#6aa8ff",
  grid: "rgba(148,163,184,0.08)",
  text: "#8b98ad",
};

export const baseOptions = (height: number): DeepPartial<ChartOptions> => ({
  height,
  autoSize: true,
  layout: {
    background: { type: ColorType.Solid, color: "transparent" },
    textColor: COLORS.text,
    fontFamily: "'JetBrains Mono', ui-monospace, monospace",
    fontSize: 11,
    attributionLogo: true,
  },
  grid: { vertLines: { color: COLORS.grid }, horzLines: { color: COLORS.grid } },
  rightPriceScale: { borderVisible: false },
  timeScale: { borderVisible: false, timeVisible: true, secondsVisible: false },
  crosshair: { mode: 0 },
});

export const toSeconds = (iso: string) => Math.floor(new Date(iso).getTime() / 1000);
