import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, ApiError, type Repo } from "../api";
import {
  Empty,
  ErrorBanner,
  fmtDate,
  fmtDuration,
  fmtInt,
  isActive,
  ProgressBar,
  Spinner,
  StatusPill,
} from "../ui";

export default function Repos() {
  const [repos, setRepos] = useState<Repo[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const mounted = useRef(true);

  const refresh = useCallback(async () => {
    try {
      const list = await api.listRepos();
      if (mounted.current) {
        setRepos(list);
        setError(null);
      }
    } catch (err) {
      if (mounted.current) setError((err as Error).message);
    }
  }, []);

  useEffect(() => {
    mounted.current = true;
    void refresh();
    return () => {
      mounted.current = false;
    };
  }, [refresh]);

  const anyActive = (repos ?? []).some((r) => isActive(r.status));
  useEffect(() => {
    if (!anyActive && repos !== null) return;
    const t = setInterval(() => void refresh(), anyActive ? 1200 : 15000);
    return () => clearInterval(t);
  }, [anyActive, repos !== null, refresh]); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div>
      <div className="page-head">
        <div>
          <h1 className="page-title">Repositories</h1>
          <div className="page-sub">
            Import a repository (zip with <code>.git</code>, or a clone URL) and open its
            dashboard.
          </div>
        </div>
        <button
          className={adding ? "btn" : "btn btn-primary"}
          onClick={() => setAdding((v) => !v)}
        >
          {adding ? "Close" : "Add repository"}
        </button>
      </div>

      {error && <ErrorBanner message={error} onRetry={() => void refresh()} />}
      {adding && (
        <AddPanel
          onDone={async () => {
            setAdding(false);
            await refresh();
          }}
        />
      )}

      {repos === null ? (
        <Spinner label="Loading repositories…" />
      ) : repos.length === 0 ? (
        <Empty
          title="No repositories yet"
          hint="Upload a zipped repository (including its .git directory) or paste a clone URL to get started."
        />
      ) : (
        <div className="grid grid-repos">
          {repos.map((r) => (
            <RepoCard key={r.id} repo={r} onChanged={refresh} />
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Import panel (zip upload / clone URL)
// ---------------------------------------------------------------------------
function AddPanel({ onDone }: { onDone: () => Promise<void> }) {
  const [tab, setTab] = useState<"upload" | "clone">("upload");
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [drag, setDrag] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    setError(null);
    setBusy(true);
    try {
      if (tab === "upload") {
        if (!file) throw new Error("Choose a .zip archive first.");
        await api.uploadRepo(file, name.trim() || undefined);
      } else {
        if (!url.trim()) throw new Error("Paste a repository URL first.");
        await api.cloneRepo(url.trim(), name.trim() || undefined);
      }
      await onDone();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : (err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="card">
      <div className="card-head">
        <h2 className="card-title">Import repository</h2>
        <div className="tabs" style={{ width: 260 }}>
          <button
            className={tab === "upload" ? "active" : ""}
            onClick={() => setTab("upload")}
          >
            Zip upload
          </button>
          <button className={tab === "clone" ? "active" : ""} onClick={() => setTab("clone")}>
            Clone URL
          </button>
        </div>
      </div>

      {error && <ErrorBanner message={error} onDismiss={() => setError(null)} />}

      {tab === "upload" ? (
        <>
          <label
            className={drag ? "dropzone drag" : "dropzone"}
            onDragOver={(e) => {
              e.preventDefault();
              setDrag(true);
            }}
            onDragLeave={() => setDrag(false)}
            onDrop={(e) => {
              e.preventDefault();
              setDrag(false);
              const f = e.dataTransfer.files?.[0];
              if (f) setFile(f);
            }}
          >
            {file ? (
              <span>
                <strong>{file.name}</strong> · {(file.size / (1 << 20)).toFixed(1)} MB
              </span>
            ) : (
              <span>
                Drop the repository <code>.zip</code> here, or click to browse
              </span>
            )}
            <input
              type="file"
              accept=".zip,application/zip"
              hidden
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            />
          </label>
          <div className="field mt">
            <label>Display name (optional)</label>
            <input
              className="input"
              placeholder="e.g. cJSON"
              value={name}
              onChange={(e) => setName(e.target.value)}
              maxLength={80}
            />
          </div>
        </>
      ) : (
        <>
          <div className="field">
            <label>Repository URL (deep-cloned with full history)</label>
            <input
              className="input"
              placeholder="https://github.com/owner/project.git"
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              spellCheck={false}
            />
          </div>
          <div className="field">
            <label>Display name (optional)</label>
            <input
              className="input"
              placeholder="Defaults to the repository name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              maxLength={80}
            />
          </div>
        </>
      )}

      <div className="row" style={{ justifyContent: "flex-end" }}>
        <button className="btn btn-primary" disabled={busy} onClick={() => void submit()}>
          {busy ? <Spinner label="Starting…" /> : tab === "upload" ? "Upload & analyse" : "Clone & analyse"}
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Repository card
// ---------------------------------------------------------------------------
function RepoCard({ repo, onChanged }: { repo: Repo; onChanged: () => Promise<void> }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
      await onChanged();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const active = isActive(repo.status);

  return (
    <div className="card repo-card">
      <div className="repo-card-head">
        <div>
          <h3 className="repo-name">
            {repo.status === "ready" ? (
              <Link to={`/r/${repo.id}`}>{repo.name}</Link>
            ) : (
              repo.name
            )}
          </h3>
          <div className="repo-origin mono" title={repo.origin}>
            {repo.kind === "zip" ? `zip · ${repo.origin_label}` : repo.origin_label}
          </div>
        </div>
        <StatusPill status={repo.status} />
      </div>

      {error && <ErrorBanner message={error} onDismiss={() => setError(null)} />}
      {repo.status === "error" && repo.error && (
        <ErrorBanner
          message={repo.error}
          onRetry={() => void act(() => api.reanalyze(repo.id, repo.kind === "clone"))}
        />
      )}

      {(active || repo.status === "error") && (
        <div>
          <ProgressBar value={repo.progress} />
          <div className="phase-line mt" style={{ marginTop: 6 }}>
            <span>{active ? repo.phase || "Working…" : "Interrupted"}</span>
            <span>{Math.round((repo.progress ?? 0) * 100)}%</span>
          </div>
        </div>
      )}

      {repo.status !== "error" && (
        <div className="repo-facts">
          <div className="fact">
            <div className="fact-value">{fmtInt(repo.commit_count)}</div>
            <div className="fact-label">Commits</div>
          </div>
          <div className="fact">
            <div className="fact-value">{fmtInt(repo.obj_count)}</div>
            <div className="fact-label">Objects</div>
          </div>
          <div className="fact">
            <div className="fact-value">
              {repo.ready_ts
                ? fmtDuration(repo.ready_ts - repo.created_ts)
                : active
                  ? "…"
                  : "—"}
            </div>
            <div className="fact-label">Analyse time</div>
          </div>
        </div>
      )}

      <div className="phase-line">
        <span>
          {repo.branch ? (
            <>
              branch <code>{repo.branch}</code>
            </>
          ) : (
            "—"
          )}
        </span>
        <span>last activity {fmtDate(repo.last_commit_ts)}</span>
      </div>
      {repo.head_hash && (
        <div className="phase-line">
          <span className="mono">{repo.head_hash.slice(0, 10)}</span>
          <span>{repo.change_count ? `${fmtInt(repo.change_count)} file changes` : ""}</span>
        </div>
      )}

      <div className="repo-actions">
        {repo.status === "ready" && (
          <Link className="btn btn-primary" to={`/r/${repo.id}`}>
            Open dashboard
          </Link>
        )}
        <button
          className="btn"
          disabled={busy || active}
          title="Re-run analysis on the stored checkout (picks up mailmap / merge ops)"
          onClick={() => void act(() => api.reanalyze(repo.id, false))}
        >
          Re-analyse
        </button>
        {repo.kind === "clone" && (
          <button
            className="btn"
            disabled={busy || active}
            title="Re-fetch the source (new commits) and analyse from scratch"
            onClick={() => void act(() => api.reanalyze(repo.id, true))}
          >
            Re-import
          </button>
        )}
        <span className="spacer" />
        <button
          className="btn btn-danger"
          disabled={busy || active}
          onClick={() => {
            if (confirm(`Delete “${repo.name}” and all of its analysed data?`)) {
              void act(() => api.deleteRepo(repo.id));
            }
          }}
        >
          Delete
        </button>
      </div>
    </div>
  );
}
