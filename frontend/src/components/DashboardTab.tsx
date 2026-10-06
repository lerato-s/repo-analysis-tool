import { useMemo } from "react";
import type { Analytics } from "../api";
import {
  authorTimelineOption,
  heatmapOption,
  ownershipOption,
  timelineOption,
  topFilesOption,
  treemapOption,
} from "../charts";
import { Empty, ErrorBanner, fmtInt, fmtPct, shortPath, Spinner } from "../ui";
import Chart from "./Chart";

/** The metrics dashboard: KPI cards + visualisations for the current filter. */
export default function DashboardTab({
  analytics,
  loading,
  error,
  onScope,
  onRetry,
}: {
  analytics: Analytics | null;
  loading: boolean;
  error: string | null;
  onScope: (path: string) => void;
  onRetry: () => void;
}) {
  if (error) return <ErrorBanner message={error} onRetry={onRetry} />;
  if (!analytics)
    return (
      <div className="card">
        <Spinner label="Computing metrics…" />
      </div>
    );
  if (analytics.kpis.n_commits === 0)
    return (
      <Empty
        title="No commits match these filters"
        hint="Adjust the commit set (time range / list), author selection or object scope."
      />
    );
  return <Charts a={analytics} loading={loading} onScope={onScope} />;
}

function Kpi({
  label,
  value,
  hint,
  tone,
}: {
  label: string;
  value: string;
  hint?: string;
  tone?: "good" | "bad";
}) {
  return (
    <div className="kpi">
      <div className={`kpi-value ${tone ?? ""}`}>{value}</div>
      <div className="kpi-label">{label}</div>
      {hint && <div className="kpi-hint">{hint}</div>}
    </div>
  );
}

function Charts({
  a,
  loading,
  onScope,
}: {
  a: Analytics;
  loading: boolean;
  onScope: (path: string) => void;
}) {
  const timeline = useMemo(() => timelineOption(a), [a]);
  const treemap = useMemo(
    () => treemapOption(a, a.filter.path, a.filter.path !== ""),
    [a],
  );
  const topFiles = useMemo(() => topFilesOption(a), [a]);
  const ownership = useMemo(() => ownershipOption(a), [a]);
  const authorTl = useMemo(() => authorTimelineOption(a), [a]);
  const heatmap = useMemo(() => heatmapOption(a), [a]);

  const k = a.kpis;
  const o = k.object;
  const scope = a.filter.path;
  const scopeLabel = shortPath(scope);
  const scopeKind = o.is_file ? "file" : scope ? "directory subtree" : "repository root";
  const capNote = k.author_count > a.authors.length;

  return (
    <div className={loading ? "updating" : undefined}>
      <div className="card">
        <div className="card-head">
          <h2 className="card-title">
            Object metrics · <span className="mono">{scopeLabel}</span>
          </h2>
          <span className="card-sub">
            {scopeKind} · {fmtInt(k.n_commits)} commits in the set
          </span>
        </div>
        <div className="kpis">
          <Kpi label="Added lines" value={fmtInt(o.added)} />
          <Kpi label="Removed lines" value={fmtInt(o.removed)} />
          <Kpi
            label="Growth"
            value={`${o.growth >= 0 ? "+" : ""}${fmtInt(o.growth)}`}
            tone={o.growth >= 0 ? "good" : "bad"}
          />
          <Kpi label="Churn" value={fmtInt(o.churn)} hint="added + removed" />
          <Kpi
            label="Modifications"
            value={fmtInt(o.mods)}
            hint={`${fmtPct(o.freq, 1)} of commits`}
          />
          <Kpi label="Churn rate" value={o.rate.toFixed(2)} hint="lines / commit" />
          {o.file_count !== undefined && !o.is_file && (
            <Kpi label="Files" value={fmtInt(o.file_count)} hint="in subtree" />
          )}
        </div>
        <div className="phase-line mt">
          <span>
            Repository context (no scope): +{fmtInt(k.repo_totals.added)} · −
            {fmtInt(k.repo_totals.removed)} · churn {fmtInt(k.repo_totals.churn)}
          </span>
          <span>
            {fmtInt(k.file_count)} files · {fmtInt(k.dir_count)} directories ·{" "}
            {fmtInt(k.author_count)} author groups
            {capNote ? " (charts show the top 500)" : ""}
          </span>
        </div>
      </div>

      {a.truncated && (
        <div className="banner mt">
          This repository exceeds the server’s directory cap — charts are based on the top
          entries.
        </div>
      )}

      <div className="grid mt">
        <div className="card">
          <div className="card-head">
            <h3 className="card-title">Line churn over time</h3>
            <span className="card-sub">
              {a.timeline.granularity} buckets · added / removed lines, commits as line
            </span>
          </div>
          <Chart option={timeline} height={280} />
        </div>

        <div className="grid grid-2">
          <div className="card">
            <div className="card-head">
              <h3 className="card-title">Directory churn</h3>
              <span className="card-sub">click a tile to scope the view</span>
            </div>
            <Chart option={treemap.option} height={treemap.height} onClick={onScope} />
          </div>
          <div className="card">
            <div className="card-head">
              <h3 className="card-title">Top files by churn</h3>
              <span className="card-sub">click a bar to scope the view</span>
            </div>
            <Chart option={topFiles} height={380} onClick={onScope} />
          </div>
        </div>

        <div className="grid grid-2">
          <div className="card">
            <div className="card-head">
              <h3 className="card-title">Author ownership</h3>
              <span className="card-sub">share of churn</span>
            </div>
            <Chart option={ownership} height={330} />
          </div>
          <div className="card">
            <div className="card-head">
              <h3 className="card-title">Author activity</h3>
              <span className="card-sub">churn of the top authors per bucket</span>
            </div>
            <Chart option={authorTl} height={330} />
          </div>
        </div>

        <div className="card">
          <div className="card-head">
            <h3 className="card-title">File volatility heatmap</h3>
            <span className="card-sub">top files × time buckets (churn)</span>
          </div>
          <Chart option={heatmap.option} height={heatmap.height} />
        </div>
      </div>
    </div>
  );
}
