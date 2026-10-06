import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, type Analytics, type AuthorGroup, type Repo } from "../api";
import AuthorsTab from "../components/AuthorsTab";
import CommitsTab from "../components/CommitsTab";
import DashboardTab from "../components/DashboardTab";
import FilterBar from "../components/FilterBar";
import ObjectsTab from "../components/ObjectsTab";
import { EMPTY_FILTER, filterKey, toBody, type FilterState } from "../filters";
import {
  ErrorBanner,
  fmtDate,
  fmtInt,
  isActive,
  ProgressBar,
  Spinner,
  StatusPill,
} from "../ui";

type TabId = "dashboard" | "objects" | "commits" | "authors";

const TABS: { id: TabId; label: string }[] = [
  { id: "dashboard", label: "Dashboard" },
  { id: "objects", label: "Files & directories" },
  { id: "commits", label: "Commits" },
  { id: "authors", label: "Authors" },
];

/**
 * Dashboard shell for a single repository: repository polling, the shared
 * filter bar, tab navigation and the analytics fetch that drives every view.
 */
export default function RepoDashboard() {
  const { repoId } = useParams();
  const id = Number(repoId);

  const [repo, setRepo] = useState<Repo | null>(null);
  const [repoError, setRepoError] = useState<string | null>(null);
  const [tab, setTab] = useState<TabId>("dashboard");

  const [filter, setFilter] = useState<FilterState>(EMPTY_FILTER);
  const [analytics, setAnalytics] = useState<Analytics | null>(null);
  const [analyticsError, setAnalyticsError] = useState<string | null>(null);
  const [fetching, setFetching] = useState(false);
  // Bumped to force the analytics + authors fetches (retry, merges, reanalysis).
  const [reloadTick, setReloadTick] = useState(0);

  const [authors, setAuthors] = useState<AuthorGroup[] | null>(null);

  const patch = useCallback(
    (p: Partial<FilterState>) => setFilter((f) => ({ ...f, ...p })),
    [],
  );
  const scopeTo = useCallback((path: string) => patch({ path }), [patch]);
  const key = useMemo(() => filterKey(filter), [filter]);

  // --- repository (polled while the analysis is running) --------------------
  useEffect(() => {
    let stop = false;
    let timer: number | undefined;
    const load = async () => {
      try {
        const row = await api.getRepo(id);
        if (stop) return;
        setRepo(row);
        setRepoError(null);
        if (isActive(row.status)) timer = window.setTimeout(load, 1200);
      } catch (err) {
        if (!stop) setRepoError((err as Error).message);
      }
    };
    void load();
    return () => {
      stop = true;
      if (timer) clearTimeout(timer);
    };
  }, [id]);

  // Reset every view when the user switches repository.
  useEffect(() => {
    setFilter(EMPTY_FILTER);
    setAnalytics(null);
    setAnalyticsError(null);
    setAuthors(null);
    setTab("dashboard");
  }, [id]);

  const ready = repo !== null && repo.id === id && repo.status === "ready";

  // Quality-of-life: "/" focuses the search box of the active tab.
  useEffect(() => {
    if (!ready) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "/" || e.metaKey || e.ctrlKey || e.altKey) return;
      const target = e.target as HTMLElement | null;
      const tag = target?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      const box = document.querySelector<HTMLInputElement>(`[data-search="${tab}"]`);
      if (box) {
        e.preventDefault();
        box.focus();
        box.select();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [ready, tab]);

  // --- authors (for the picker) ----------------------------------------------
  useEffect(() => {
    if (!ready || !repo) return;
    let cancel = false;
    api
      .authors(repo.id)
      .then((p) => {
        if (!cancel) setAuthors(p.groups);
      })
      .catch(() => {
        if (!cancel) setAuthors([]);
      });
    return () => {
      cancel = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, repo?.id, repo?.data_version, reloadTick]);

  // --- analytics for the current filter --------------------------------------
  useEffect(() => {
    if (!ready || !repo) return;
    let cancel = false;
    setFetching(true);
    api
      .analytics(repo.id, toBody(filter))
      .then((a) => {
        if (cancel) return;
        setAnalytics(a);
        setAnalyticsError(null);
      })
      .catch((err) => {
        if (cancel) return;
        setAnalyticsError((err as Error).message);
      })
      .finally(() => {
        if (!cancel) setFetching(false);
      });
    return () => {
      cancel = true;
    };
    // `key` captures the whole filter body, so the filter object itself is not
    // needed as a dependency; `reloadTick` forces a refetch.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, repo?.id, repo?.data_version, key, reloadTick]);

  // Scope suggestions: all directories + the files charts already know about.
  const dirOptions = useMemo(
    () => (analytics?.dirs ?? []).map((d) => d.path).filter(Boolean),
    [analytics],
  );
  const fileOptions = useMemo(() => {
    if (!analytics) return [];
    const set = new Set<string>();
    for (const f of analytics.top_files) set.add(f.path);
    for (const p of analytics.heatmap.files) set.add(p);
    return [...set].sort();
  }, [analytics]);

  if (repoError && !repo) {
    return <ErrorBanner message={repoError} />;
  }
  if (!repo || repo.id !== id) {
    return <Spinner label="Loading repository…" />;
  }
  if (repo.status === "error") {
    return (
      <div>
        <RepoHeader repo={repo} />
        <ErrorBanner message={repo.error ?? "Analysis failed."} />
      </div>
    );
  }
  if (repo.status !== "ready") {
    return (
      <div>
        <RepoHeader repo={repo} />
        <div className="card">
          <div className="card-head">
            <h2 className="card-title">Analysing…</h2>
            <span className="muted">{Math.round((repo.progress ?? 0) * 100)}%</span>
          </div>
          <ProgressBar value={repo.progress} />
          <div className="phase-line mt">
            <span>{repo.phase || "Working…"}</span>
          </div>
        </div>
      </div>
    );
  }

  const listMode = filter.mode === "list";
  return (
    <div>
      <RepoHeader repo={repo} />
      <FilterBar
        state={filter}
        onChange={patch}
        authors={authors}
        dirs={dirOptions}
        files={fileOptions}
        onOpenCommits={() => setTab("commits")}
      />
      <div className="tabs-row mt">
        <div className="tabs" role="tablist" aria-label="Views">
          {TABS.map((t) => (
            <button
              key={t.id}
              role="tab"
              aria-selected={tab === t.id}
              className={tab === t.id ? "active" : ""}
              onClick={() => setTab(t.id)}
            >
              {t.label}
              {t.id === "commits" && listMode && filter.hashes.length > 0 && (
                <span className="tab-badge">{fmtInt(filter.hashes.length)}</span>
              )}
            </button>
          ))}
        </div>
        <span className="spacer" />
        {fetching && <Spinner label="Updating…" />}
      </div>

      {tab === "dashboard" && (
        <DashboardTab
          analytics={analytics}
          loading={fetching}
          error={analyticsError}
          onScope={scopeTo}
          onRetry={() => setReloadTick((t) => t + 1)}
        />
      )}
      {tab === "objects" && (
        <ObjectsTab
          repoId={repo.id}
          body={toBody(filter)}
          scopePath={filter.path}
          onScope={scopeTo}
          onClearScope={() => patch({ path: "" })}
        />
      )}
      {tab === "commits" && (
        <CommitsTab
          repoId={repo.id}
          filter={filter}
          onPatch={patch}
          refreshTick={reloadTick}
        />
      )}
      {tab === "authors" && (
        <AuthorsTab
          repoId={repo.id}
          refreshTick={reloadTick}
          onDataChanged={() => {
            // Grouping changed: refetch analytics and drop now-stale author keys.
            setReloadTick((t) => t + 1);
            patch({ authorKeys: [] });
          }}
        />
      )}
    </div>
  );
}

function RepoHeader({ repo }: { repo: Repo }) {
  return (
    <div className="page-head">
      <div>
        <h1 className="page-title">
          {repo.name} <StatusPill status={repo.status} />
        </h1>
        <div className="page-sub">
          {repo.kind === "clone" ? repo.origin_label : `zip · ${repo.origin_label}`} ·{" "}
          {fmtInt(repo.commit_count)} commits · {fmtInt(repo.obj_count)} objects ·{" "}
          {fmtDate(repo.first_commit_ts)} → {fmtDate(repo.last_commit_ts)}
        </div>
      </div>
      <Link className="btn" to="/">
        All repositories
      </Link>
    </div>
  );
}
