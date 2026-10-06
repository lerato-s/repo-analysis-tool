/** ECharts option builders for the dashboard charts (dark theme baked in). */
import type { EChartsOption } from "echarts";
import type { Analytics } from "./api";

const AXIS = "#8b93a7";
const LINE = "#232b3d";
const SPLIT = "#1b2231";
const PALETTE = [
  "#4f8cff",
  "#3ecf8e",
  "#f4b860",
  "#ff6b6b",
  "#b48aff",
  "#59b0ff",
  "#f78fb3",
  "#8ce99a",
  "#ffd43b",
  "#74c0fc",
];

const base = (): EChartsOption => ({
  textStyle: { color: "#e6e9f0", fontSize: 12 },
  animationDuration: 250,
  tooltip: {
    backgroundColor: "#171c28",
    borderColor: LINE,
    textStyle: { color: "#e6e9f0", fontSize: 12 },
  },
});

const shortLabel = (s: string, n = 24): string =>
  s.length <= n ? s : `…${s.slice(s.length - n + 1)}`;

// ---------------------------------------------------------------------------
// Timeline: added / removed bars + commit-count line
// ---------------------------------------------------------------------------
export function timelineOption(a: Analytics): EChartsOption {
  const pts = a.timeline.points;
  return {
    ...base(),
    grid: { left: 56, right: 46, top: 34, bottom: 30 },
    legend: {
      top: 0,
      textStyle: { color: AXIS },
      data: ["added", "removed", "commits"],
    },
    tooltip: { ...base().tooltip, trigger: "axis" },
    xAxis: {
      type: "category",
      data: pts.map((p) => p.b),
      axisLabel: { color: AXIS, hideOverlap: true },
      axisLine: { lineStyle: { color: LINE } },
    },
    yAxis: [
      {
        type: "value",
        name: "lines",
        nameTextStyle: { color: AXIS },
        axisLabel: { color: AXIS, formatter: (v: number) => compact(v) },
        splitLine: { lineStyle: { color: SPLIT } },
      },
      {
        type: "value",
        name: "commits",
        nameTextStyle: { color: AXIS },
        axisLabel: { color: AXIS, formatter: (v: number) => compact(v) },
        splitLine: { show: false },
      },
    ],
    series: [
      {
        name: "added",
        type: "bar",
        stack: "lines",
        itemStyle: { color: "#3ecf8e" },
        data: pts.map((p) => p.added),
      },
      {
        name: "removed",
        type: "bar",
        stack: "lines",
        itemStyle: { color: "#ff6b6b" },
        data: pts.map((p) => p.removed),
      },
      {
        name: "commits",
        type: "line",
        yAxisIndex: 1,
        symbol: "none",
        lineStyle: { color: "#4f8cff", width: 2 },
        itemStyle: { color: "#4f8cff" },
        data: pts.map((p) => p.commits),
      },
    ],
  };
}

// ---------------------------------------------------------------------------
// Treemap of directories by churn (click a node to scope the view)
// ---------------------------------------------------------------------------
interface TreeNode {
  name: string;
  value: number;
  children?: TreeNode[];
}

export function treemapOption(
  a: Analytics,
  scopePath: string,
  onScoped: boolean,
): { option: EChartsOption; height: number } {
  const dirs = a.dirs;
  const inScope = (p: string): boolean =>
    scopePath === "" || p === scopePath || p.startsWith(scopePath + "/");

  const byPath = new Map<string, TreeNode>();
  for (const d of dirs) {
    if (!inScope(d.path)) continue;
    byPath.set(d.path, { name: d.path === "" ? "/" : d.path, value: d.churn });
  }
  const rootKey = scopePath;
  if (!byPath.has(rootKey)) {
    byPath.set(rootKey, {
      name: rootKey === "" ? "/" : rootKey,
      value: a.kpis.object.churn,
    });
  }
  const depths = [...byPath.keys()].sort((x, y) => x.split("/").length - y.split("/").length);
  for (const p of depths) {
    if (p === rootKey) continue;
    const cut = p.lastIndexOf("/");
    const parent = cut === -1 ? "" : p.slice(0, cut);
    const parentNode = byPath.get(parent) ?? byPath.get(rootKey);
    parentNode?.children?.push(byPath.get(p)!);
    if (parentNode && !parentNode.children) parentNode.children = [byPath.get(p)!];
  }
  const root = byPath.get(rootKey)!;

  return {
    option: {
      ...base(),
      tooltip: {
        ...base().tooltip,
        formatter: (params: unknown) => {
          const p = params as { name: string; value: number };
          return `<b>${p.name}</b><br/>churn ${p.value.toLocaleString("en-US")}`;
        },
      },
      series: [
        {
          type: "treemap",
          data: root.children?.length ? root.children : [root],
          roam: false,
          nodeClick: false,
          breadcrumb: { show: false },
          top: 6,
          bottom: 6,
          left: 4,
          right: 4,
          label: {
            show: true,
            color: "#fff",
            fontSize: 12,
            formatter: (p: unknown) => {
              const name = (p as { name: string }).name;
              return name === "/" ? "/" : name.split("/").pop() || "/";
            },
          },
          upperLabel: { show: false },
          itemStyle: { borderColor: "#0b0e14", borderWidth: 2, gapWidth: 2 },
          levels: [
            { itemStyle: { borderWidth: 3, gapWidth: 3 } },
            { colorSaturation: [0.35, 0.75] },
          ],
          color: PALETTE,
          visualDimension: 0,
        },
      ],
    },
    height: onScoped ? 300 : 360,
  };
}

// ---------------------------------------------------------------------------
// Top files by churn
// ---------------------------------------------------------------------------
export function topFilesOption(a: Analytics): EChartsOption {
  const files = a.top_files;
  return {
    ...base(),
    grid: { left: 8, right: 64, top: 8, bottom: 24, containLabel: true },
    tooltip: {
      ...base().tooltip,
      formatter: (params: unknown) => {
        const p = params as { name: string; value: number };
        return `<b>${p.name}</b><br/>churn ${p.value.toLocaleString("en-US")}`;
      },
    },
    xAxis: {
      type: "value",
      axisLabel: { color: AXIS, formatter: (v: number) => compact(v) },
      splitLine: { lineStyle: { color: SPLIT } },
    },
    yAxis: {
      type: "category",
      inverse: true,
      data: files.map((f) => shortLabel(f.path, 34)),
      axisLabel: {
        color: AXIS,
        fontSize: 11.5,
      },
      axisLine: { lineStyle: { color: LINE } },
    },
    series: [
      {
        type: "bar",
        data: files.map((f) => ({
          name: f.path,
          value: f.churn,
        })),
        itemStyle: {
          borderRadius: [0, 4, 4, 0],
          color: {
            type: "linear",
            x: 0,
            y: 0,
            x2: 1,
            y2: 0,
            colorStops: [
              { offset: 0, color: "#2f5fb8" },
              { offset: 1, color: "#4f8cff" },
            ],
          },
        },
        label: {
          show: true,
          position: "right",
          color: AXIS,
          formatter: (p: unknown) => compact((p as { value: number }).value),
        },
      },
    ],
  };
}

// ---------------------------------------------------------------------------
// Author ownership (donut, top 10 + others)
// ---------------------------------------------------------------------------
export function ownershipOption(a: Analytics): EChartsOption {
  const authors = a.authors.filter((x) => x.churn > 0);
  const top = authors.slice(0, 10);
  const restChurn = authors.slice(10).reduce((s, x) => s + x.churn, 0);
  const data = [
    ...top.map((x, i) => ({
      name: x.label,
      value: x.churn,
      itemStyle: { color: PALETTE[i % PALETTE.length] },
    })),
    ...(restChurn > 0
      ? [
          {
            name: `others (${authors.length - 10})`,
            value: restChurn,
            itemStyle: { color: "#39415a" },
          },
        ]
      : []),
  ];
  return {
    ...base(),
    tooltip: {
      ...base().tooltip,
      formatter: (params: unknown) => {
        const p = params as { name: string; value: number; percent: number };
        return `<b>${p.name}</b><br/>churn ${p.value.toLocaleString("en-US")} · ${p.percent}%`;
      },
    },
    legend: {
      type: "scroll",
      orient: "vertical",
      right: 6,
      top: "middle",
      textStyle: { color: AXIS, fontSize: 11.5 },
      formatter: (name: string) => shortLabel(name, 26),
    },
    series: [
      {
        type: "pie",
        radius: ["42%", "72%"],
        center: ["32%", "50%"],
        data,
        label: { show: false },
        emphasis: { scale: true, scaleSize: 4 },
      },
    ],
  };
}

// ---------------------------------------------------------------------------
// Stacked author timeline (top authors by scoped churn)
// ---------------------------------------------------------------------------
export function authorTimelineOption(a: Analytics): EChartsOption {
  const { authors, points } = a.author_timeline;
  if (!authors.length) return { ...base() };
  return {
    ...base(),
    grid: { left: 56, right: 16, top: 34, bottom: 30 },
    legend: {
      type: "scroll",
      top: 0,
      textStyle: { color: AXIS, fontSize: 11.5 },
      formatter: (name: string) => shortLabel(name, 22),
    },
    tooltip: { ...base().tooltip, trigger: "axis" },
    xAxis: {
      type: "category",
      data: points.map((p) => p.b),
      axisLabel: { color: AXIS, hideOverlap: true },
      axisLine: { lineStyle: { color: LINE } },
    },
    yAxis: {
      type: "value",
      axisLabel: { color: AXIS, formatter: (v: number) => compact(v) },
      splitLine: { lineStyle: { color: SPLIT } },
    },
    series: authors.map((au, i) => ({
      name: au.label,
      type: "bar" as const,
      stack: "authors",
      itemStyle: { color: PALETTE[i % PALETTE.length] },
      data: points.map((p) => p.values[i] ?? 0),
    })),
  };
}

// ---------------------------------------------------------------------------
// Heatmap: top files x time buckets
// ---------------------------------------------------------------------------
export function heatmapOption(a: Analytics): { option: EChartsOption; height: number } {
  const { buckets, files, values } = a.heatmap;
  const max = values.reduce((m, v) => Math.max(m, v[2]), 0);
  return {
    option: {
      ...base(),
      grid: { left: 210, right: 18, top: 12, bottom: 26 },
      tooltip: {
        ...base().tooltip,
        formatter: (params: unknown) => {
          const p = params as { value: [number, number, number] };
          const [b, f, v] = p.value;
          return `<b>${files[f]}</b><br/>${buckets[b]} · churn ${v.toLocaleString("en-US")}`;
        },
      },
      xAxis: {
        type: "category",
        data: buckets,
        axisLabel: { color: AXIS, hideOverlap: true },
        axisLine: { lineStyle: { color: LINE } },
        splitArea: { show: false },
      },
      yAxis: {
        type: "category",
        inverse: true,
        data: files.map((f) => shortLabel(f, 32)),
        axisLabel: { color: AXIS, fontSize: 11.5 },
        axisLine: { lineStyle: { color: LINE } },
      },
      visualMap: {
        min: 0,
        max: Math.max(1, max),
        calculable: true,
        orient: "horizontal",
        right: 8,
        bottom: 0,
        itemWidth: 10,
        itemHeight: 90,
        textStyle: { color: AXIS, fontSize: 11 },
        inRange: { color: ["#141c2e", "#2f5fb8", "#4f8cff", "#f4b860", "#ff6b6b"] },
      },
      series: [
        {
          type: "heatmap",
          data: values.map(([f, b, v]) => [b, f, v]),
          itemStyle: { borderColor: "#0b0e14", borderWidth: 1 },
          emphasis: { itemStyle: { borderColor: "#fff", borderWidth: 1 } },
        },
      ],
    },
    height: Math.max(220, files.length * 26 + 80),
  };
}

const compact = (v: number): string =>
  Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 }).format(v);
