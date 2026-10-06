import { BarChart, HeatmapChart, LineChart, PieChart, TreemapChart } from "echarts/charts";
import {
  AxisPointerComponent,
  GridComponent,
  LegendComponent,
  LegendScrollComponent,
  TooltipComponent,
  VisualMapComponent,
} from "echarts/components";
import * as echarts from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import type { EChartsOption } from "echarts";
import { useEffect, useRef } from "react";

// Modular registration keeps the bundle small (only the charts we render).
echarts.use([
  BarChart,
  LineChart,
  PieChart,
  TreemapChart,
  HeatmapChart,
  GridComponent,
  TooltipComponent,
  AxisPointerComponent,
  LegendComponent,
  LegendScrollComponent,
  VisualMapComponent,
  CanvasRenderer,
]);

/** Thin ECharts wrapper: themes, resize handling and option updates. */
export default function Chart({
  option,
  height = 320,
  onClick,
}: {
  option: EChartsOption;
  height?: number;
  onClick?: (name: string) => void;
}) {
  const host = useRef<HTMLDivElement>(null);
  const chart = useRef<ReturnType<typeof echarts.init> | null>(null);

  useEffect(() => {
    if (!host.current) return;
    const inst = echarts.init(host.current, undefined, { renderer: "canvas" });
    chart.current = inst;
    const ro = new ResizeObserver(() => inst.resize());
    ro.observe(host.current);
    return () => {
      ro.disconnect();
      inst.dispose();
      chart.current = null;
    };
  }, []);

  useEffect(() => {
    chart.current?.setOption(option, { notMerge: true });
  }, [option]);

  useEffect(() => {
    const inst = chart.current;
    if (!inst || !onClick) return;
    const handler = (params: unknown) => {
      const p = params as { data?: { name?: string }; name?: string };
      const name = p?.data?.name ?? p?.name;
      if (name) onClick(name);
    };
    inst.on("click", handler);
    return () => {
      inst.off("click", handler);
    };
  }, [onClick]);

  return <div ref={host} style={{ width: "100%", height }} />;
}
