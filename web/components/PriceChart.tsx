"use client";

import { useEffect, useRef } from "react";
import {
  CandlestickSeries,
  LineSeries,
  createChart,
  createSeriesMarkers,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";
import type { Candle, OverlayLine, Trade } from "@/lib/types";
import { COLORS, OVERLAY_PALETTE, baseOptions, toSeconds } from "./charts";

export default function PriceChart({
  candles,
  overlays,
  trades,
}: {
  candles: Candle[];
  overlays: OverlayLine[];
  trades: Trade[];
}) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!ref.current) return;
    const chart = createChart(ref.current, baseOptions(380));
    const price = chart.addSeries(CandlestickSeries, {
      upColor: COLORS.up, downColor: COLORS.down, borderVisible: false,
      wickUpColor: COLORS.up, wickDownColor: COLORS.down,
    });
    price.setData(candles.map((c) => ({
      time: c.time as UTCTimestamp, open: c.open, high: c.high, low: c.low, close: c.close,
    })));

    overlays.forEach((ov, i) => {
      const s = chart.addSeries(LineSeries, {
        color: ov.color || OVERLAY_PALETTE[i % OVERLAY_PALETTE.length],
        lineWidth: 2, priceLineVisible: false, lastValueVisible: false,
        crosshairMarkerVisible: false,
      });
      s.setData(ov.data.map((p) => ({ time: p.time as UTCTimestamp, value: p.value })));
    });

    const markers: SeriesMarker<Time>[] = [];
    for (const t of trades) {
      const short = t.side === "short";
      const result = `${t.return_pct >= 0 ? "+" : ""}${t.return_pct.toFixed(1)}%`;
      const won = t.pnl >= 0 ? COLORS.up : COLORS.down;
      // Long: buy arrow up below the bar, exit arrow down above. Short: the mirror image.
      markers.push({ time: toSeconds(t.entry_time) as UTCTimestamp,
        position: short ? "aboveBar" : "belowBar", color: short ? COLORS.down : COLORS.up,
        shape: short ? "arrowDown" : "arrowUp", ...(short ? { text: "SAT" } : {}) });
      markers.push({ time: toSeconds(t.exit_time) as UTCTimestamp,
        position: short ? "belowBar" : "aboveBar", color: won,
        shape: short ? "arrowUp" : "arrowDown", text: result });
    }
    markers.sort((a, b) => (a.time as number) - (b.time as number));
    createSeriesMarkers(price, markers);
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [candles, overlays, trades]);

  return <div ref={ref} className="chart" />;
}
