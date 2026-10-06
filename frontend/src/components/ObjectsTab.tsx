import { useEffect, useMemo, useState } from "react";
import { api, type FileRow, type FilterBody, type ObjectsRow, type TopAuthor } from "../api";
import { Empty, ErrorBanner, fmtInt, fmtPct, Spinner } from "../ui";

const PAGE = 50;

/** Directory rows are the only ones carrying a subtree file count. */
const isFileRow = (r: ObjectsRow): r is FileRow & { tops: TopAuthor[] } => r.is_file;

type Kind = "file" | "dir";
type SortKey =
  | "path"
  | "added"
  | "removed"
  | "growth"
  | "churn"
  | "mods"
  | "freq"
  | "rate"
  | "file_count";

interface Sort {
  key: SortKey;
  order: "asc" | "desc";
}

/** Files & directories metric table for the current filter + scope. */
export default function ObjectsTab({
  repoId,
  body,
  scopePath,
  onScope,
  onClearScope,
}: {
  repoId: number;
  body: Partial<FilterBody>;
  scopePath: string;
  onScope: (path: string) => void;
  onClearScope: () => void;
}) {
  const [kind, setKind] = useState<Kind>("file");
  const [query, setQuery] = useState("");
  const [debounced, setDebounced] = useState("");
  const [sort, setSort] = useState<Sort>({ key: "churn", order: "desc" });
  const [page, setPage] = useState(0);

  const [data, setData] = useState<{ total: number; rows: ObjectsRow[] } | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const bodyKey = useMemo(() => JSON.stringify(body), [body]);

  useEffect(() => {
    const t = window.setTimeout(() => setDebounced(query.trim()), 250);
    return () => window.clearTimeout(t);
  }, [query]);

  // Any change to the inputs rewinds to the first page.
  useEffect(() => {
    setPage(0);
  }, [kind, debounced, sort.key, sort.order, bodyKey]);

  const fetchKey = `${repoId}|${kind}|${debounced}|${sort.key}|${sort.order}|${page}|${bodyKey}`;
  useEffect(() => {
    let cancel = false;
    setLoading(true);
    api
      .objects(repoId, body, {
        kind,
        q: debounced || undefined,
        sort: sort.key,
        order: sort.order,
        offset: page * PAGE,
        limit: PAGE,
      })
      .then((res) => {
        if (cancel) return;
        setData(res);
        setError(null);
      })
      .catch((err) => {
        if (!cancel) setError((err as Error).message);
      })
      .finally(() => {
        if (!cancel) setLoading(false);
      });
    return () => {
      cancel = true;
    };
    // `fetchKey` captures every input; `body` is intentionally not a direct dep.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fetchKey]);

  const toggleSort = (key: SortKey) =>
    setSort((s) =>
      s.key === key
        ? { key, order: s.order === "asc" ? "desc" : "asc" }
        : { key, order: key === "path" ? "asc" : "desc" },
    );

  const total = data?.total ?? 0;
  const rows = data?.rows ?? [];
  const pages = Math.max(1, Math.ceil(total / PAGE));
  const fileScope = scopePath !== "" && kind === "dir" && total === 0;

  const head = (label: string, key: SortKey, num = false) => (
    <th
      className={`sortable ${num ? "num" : ""}`}
      onClick={() => toggleSort(key)}
      title="Sort"
    >
      {label}
      {sort.key === key && <span className="sort-arrow">{sort.order === "asc" ? "▲" : "▼"}</span>}
    </th>
  );

  return (
    <div className={`card ${loading && data ? "updating" : ""}`}>
      <div className="card-head">
        <div className="row wrap">
          <div className="seg" role="group" aria-label="Object kind">
            <button className={kind === "file" ? "active" : ""} onClick={() => setKind("file")}>
              Files
            </button>
            <button className={kind === "dir" ? "active" : ""} onClick={() => setKind("dir")}>
              Directories
            </button>
          </div>
          <input
            className="input input-inline"
            placeholder="Filter by path…  ( / )"
            value={query}
            spellCheck={false}
            data-search="objects"
            onChange={(e) => setQuery(e.target.value)}
          />
          {query && (
            <button className="btn btn-small btn-ghost" onClick={() => setQuery("")}>
              Clear
            </button>
          )}
        </div>
        <div className="row">
          {loading && data && <Spinner />}
          <span className="muted">
            {fmtInt(total)} {kind === "file" ? "file" : "director"}
            {kind === "file" ? (total === 1 ? "" : "s") : total === 1 ? "y" : "ies"}
            {scopePath && (
              <>
                {" "}
                within <span className="mono">{scopePath}</span>
              </>
            )}
          </span>
          {scopePath && (
            <button className="btn btn-small btn-ghost" onClick={onClearScope}>
              Clear scope
            </button>
          )}
        </div>
      </div>

      {error ? (
        <ErrorBanner
          message={error}
          onRetry={() => setSort((s) => ({ ...s }))}
        />
      ) : fileScope ? (
        <Empty
          title="This scope is a single file"
          hint="A file has no subdirectories — switch to Files, or clear the scope."
        />
      ) : !data ? (
        <div className="card">
          <Spinner label="Loading objects…" />
        </div>
      ) : rows.length === 0 ? (
        <Empty
          title="No objects match"
          hint={
            debounced
              ? `Nothing under this scope matches “${debounced}”.`
              : "This commit set produced no changes for these objects."
          }
        />
      ) : (
        <>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  {head("Path", "path")}
                  {head("+", "added", true)}
                  {head("−", "removed", true)}
                  {head("Growth", "growth", true)}
                  {head("Churn", "churn", true)}
                  {head("Mods", "mods", true)}
                  {head("Freq", "freq", true)}
                  {head("Rate", "rate", true)}
                  {kind === "dir" && head("Files", "file_count", true)}
                  <th>Top authors</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <tr key={r.path}>
                    <td className="path-cell">
                      <button
                        className="link-button"
                        title="Scope the dashboard to this object"
                        onClick={() => onScope(r.path)}
                      >
                        {r.path}
                      </button>
                      {isFileRow(r) && r.ever_binary && <span className="marker">binary</span>}
                      {isFileRow(r) && !r.in_head && <span className="marker">deleted</span>}
                      {isFileRow(r) && r.is_gitlink && <span className="marker">submodule</span>}
                    </td>
                    <td className="num">{fmtInt(r.added)}</td>
                    <td className="num">{fmtInt(r.removed)}</td>
                    <td className={`num ${r.growth > 0 ? "good" : r.growth < 0 ? "bad" : ""}`}>
                      {r.growth > 0 ? "+" : ""}
                      {fmtInt(r.growth)}
                    </td>
                    <td className="num">{fmtInt(r.churn)}</td>
                    <td className="num">{fmtInt(r.mods)}</td>
                    <td className="num muted">{fmtPct(r.freq, 0)}</td>
                    <td className="num muted">{r.rate.toFixed(1)}</td>
                    {kind === "dir" && (
                      <td className="num muted">{!isFileRow(r) ? fmtInt(r.file_count) : "—"}</td>
                    )}
                    <td>
                      {r.tops.length === 0 ? (
                        <span className="muted">—</span>
                      ) : (
                        <span className="tops">
                          {r.tops.map((t) => (
                            <span className="top-chip" key={t.key} title={t.label}>
                              {t.label} · {fmtPct(t.ownership, 0)}
                            </span>
                          ))}
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          <div className="pager">
            <span className="muted">
              {fmtInt(page * PAGE + 1)}–{fmtInt(Math.min((page + 1) * PAGE, total))} of{" "}
              {fmtInt(total)}
            </span>
            <span className="row">
              <button
                className="btn btn-small"
                disabled={page === 0}
                onClick={() => setPage((p) => Math.max(0, p - 1))}
              >
                Prev
              </button>
              <span className="muted">
                page {page + 1} / {pages}
              </span>
              <button
                className="btn btn-small"
                disabled={page + 1 >= pages}
                onClick={() => setPage((p) => p + 1)}
              >
                Next
              </button>
            </span>
          </div>
        </>
      )}
    </div>
  );
}
