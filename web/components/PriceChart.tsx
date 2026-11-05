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
import type { Candle, Trade } from "@/lib/types";
import { COLORS, baseOptions, toSeconds } from "./charts";

export default function PriceChart({ candles, trades }: { candles: Candle[]; trades: Trade[] }) {
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

    const line = (key: "ema_fast" | "ema_slow", color: string) => {
      const s = chart.addSeries(LineSeries, {
        color, lineWidth: 2, priceLineVisible: false, lastValueVisible: false,
        crosshairMarkerVisible: false,
      });
      s.setData(candles.filter((c) => c[key] !== null)
        .map((c) => ({ time: c.time as UTCTimestamp, value: c[key] as number })));
    };
    line("ema_fast", COLORS.fast);
    line("ema_slow", COLORS.slow);

    const markers: SeriesMarker<Time>[] = [];
    for (const t of trades) {
      markers.push({ time: toSeconds(t.entry_time) as UTCTimestamp, position: "belowBar",
        color: COLORS.up, shape: "arrowUp" });
      markers.push({ time: toSeconds(t.exit_time) as UTCTimestamp, position: "aboveBar",
        color: t.pnl >= 0 ? COLORS.up : COLORS.down, shape: "arrowDown",
        text: `${t.return_pct >= 0 ? "+" : ""}${t.return_pct.toFixed(1)}%` });
    }
    markers.sort((a, b) => (a.time as number) - (b.time as number));
    createSeriesMarkers(price, markers);
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [candles, trades]);

  return <div ref={ref} className="chart" />;
}
