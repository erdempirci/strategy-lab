"use client";

import { useEffect, useRef } from "react";
import { AreaSeries, createChart, type UTCTimestamp } from "lightweight-charts";
import type { BacktestResult } from "@/lib/types";
import { COLORS, baseOptions, toSeconds } from "./charts";

export default function EquityChart({ curve }: { curve: BacktestResult["equity_curve"] }) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!ref.current) return;
    const chart = createChart(ref.current, baseOptions(320));
    const equity = chart.addSeries(AreaSeries, {
      lineColor: COLORS.equity, topColor: "rgba(106,168,255,0.28)",
      bottomColor: "rgba(106,168,255,0.02)", lineWidth: 2, priceLineVisible: false,
    });
    equity.setData(curve.map((p) => ({ time: toSeconds(p.timestamp) as UTCTimestamp, value: p.equity })));

    const dd = chart.addSeries(AreaSeries, {
      lineColor: COLORS.down, topColor: "rgba(229,86,107,0.02)",
      bottomColor: "rgba(229,86,107,0.35)", lineWidth: 1, priceLineVisible: false,
      invertFilledArea: true,
      priceFormat: { type: "custom", formatter: (v: number) => `${v.toFixed(1)}%` },
    }, 1);
    dd.setData(curve.map((p) => ({ time: toSeconds(p.timestamp) as UTCTimestamp, value: p.drawdown_pct })));
    chart.panes()[1]?.setHeight(90);
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [curve]);

  return <div ref={ref} className="chart" style={{ height: 320 }} />;
}
