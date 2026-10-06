/** Shared formatting helpers and small presentational components. */
import type { RepoStatus } from "./api";

// ---------------------------------------------------------------------------
// Formatting
// ---------------------------------------------------------------------------
export const fmtInt = (n: number | null | undefined): string =>
  (n ?? 0).toLocaleString("en-US");

export const fmtCompact = (n: number | null | undefined): string =>
  Intl.NumberFormat("en-US", {
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(n ?? 0);

export const fmtDate = (ts: number | null | undefined): string =>
  ts ? new Date(ts * 1000).toLocaleDateString("en-CA") : "—";

export const fmtDateTime = (ts: number | null | undefined): string =>
  ts ? new Date(ts * 1000).toLocaleString("en-CA", { hour12: false }) : "—";

export const fmtPct = (frac: number | null | undefined, digits = 1): string =>
  `${(((frac ?? 0) * 100).toFixed(digits)).replace(/\.0+$/, "")}%`;

export const fmtDuration = (seconds: number | null | undefined): string => {
  if (seconds == null) return "—";
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s`;
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
};

export const shortPath = (path: string): string => (path === "" ? "/" : path);

// ---------------------------------------------------------------------------
// Status
// ---------------------------------------------------------------------------
const STATUS_META: Record<RepoStatus, { label: string; tone: string }> = {
  pending: { label: "Queued", tone: "muted" },
  cloning: { label: "Cloning", tone: "info" },
  extracting: { label: "Extracting", tone: "info" },
  analyzing: { label: "Analyzing", tone: "info" },
  ready: { label: "Ready", tone: "good" },
  error: { label: "Failed", tone: "bad" },
};

export const isActive = (status: RepoStatus): boolean =>
  status === "pending" ||
  status === "cloning" ||
  status === "extracting" ||
  status === "analyzing";

export function StatusPill({ status }: { status: RepoStatus }) {
  const meta = STATUS_META[status] ?? { label: status, tone: "muted" };
  return (
    <span className={`pill pill-${meta.tone}`}>
      {isActive(status) && <span className="pulse" />}
      {meta.label}
    </span>
  );
}

export function ProgressBar({ value }: { value: number }) {
  const pct = Math.max(0, Math.min(1, value)) * 100;
  return (
    <div className="progress" role="progressbar" aria-valuenow={pct}>
      <div className="progress-fill" style={{ width: `${pct}%` }} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Feedback
// ---------------------------------------------------------------------------
export function ErrorBanner({
  message,
  onRetry,
  onDismiss,
}: {
  message: string;
  onRetry?: () => void;
  onDismiss?: () => void;
}) {
  return (
    <div className="banner banner-bad" role="alert">
      <span className="banner-icon">!</span>
      <span className="banner-text">{message}</span>
      {onRetry && (
        <button className="btn btn-small" onClick={onRetry}>
          Retry
        </button>
      )}
      {onDismiss && (
        <button className="btn btn-small btn-ghost" onClick={onDismiss}>
          Dismiss
        </button>
      )}
    </div>
  );
}

export function Spinner({ label }: { label?: string }) {
  return (
    <span className="spinner-wrap">
      <span className="spinner" />
      {label && <span className="muted">{label}</span>}
    </span>
  );
}

export function Empty({
  title,
  hint,
  children,
}: {
  title: string;
  hint?: string;
  children?: React.ReactNode;
}) {
  return (
    <div className="empty">
      <div className="empty-title">{title}</div>
      {hint && <div className="empty-hint">{hint}</div>}
      {children}
    </div>
  );
}
