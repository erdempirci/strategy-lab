import { ColorType, type DeepPartial, type ChartOptions } from "lightweight-charts";

export const COLORS = {
  // Binance trading colours: up/down carry direction only; yellow is the single accent.
  up: "#0ecb81",
  down: "#f6465d",
  fast: "#fcd535",
  slow: "#3b82f6",
  equity: "#fcd535",
  grid: "rgba(43,49,57,0.6)",
  text: "#929aa5",
};

export const baseOptions = (height: number): DeepPartial<ChartOptions> => ({
  height,
  autoSize: true,
  layout: {
    background: { type: ColorType.Solid, color: "transparent" },
    textColor: COLORS.text,
    fontFamily: "'IBM Plex Mono', ui-monospace, monospace",
    fontSize: 11,
    attributionLogo: true,
  },
  grid: { vertLines: { color: COLORS.grid }, horzLines: { color: COLORS.grid } },
  rightPriceScale: { borderVisible: false },
  timeScale: { borderVisible: false, timeVisible: true, secondsVisible: false },
  crosshair: { mode: 0 },
});

export const OVERLAY_PALETTE = ["#fcd535", "#3b82f6", "#2dbdb6", "#929aa5"];

export const toSeconds = (iso: string) => Math.floor(new Date(iso).getTime() / 1000);
