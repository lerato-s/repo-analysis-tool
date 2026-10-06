import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { api, type CommitDetail, type CommitRow } from "../api";
import { Empty, ErrorBanner, fmtDateTime, fmtInt, Spinner } from "../ui";
import type { FilterState } from "../filters";

const PAGE = 50;

/** Commit explorer: search, browse and hand-pick commits as a custom commit
 * set (live — the selection *is* the global "Commit list" filter). */
export default function CommitsTab({
  repoId,
  filter,
  onPatch,
  refreshTick,
}: {
  repoId: number;
  filter: FilterState;
  onPatch: (p: Partial<FilterState>) => void;
  refreshTick: number;
}) {
  const [query, setQuery] = useState("");
  const [debounced, setDebounced] = useState("");
  const [order, setOrder] = useState<"asc" | "desc">("desc");
  const [page, setPage] = useState(0);

  const [data, setData] = useState<{ total: number; rows: CommitRow[] } | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [selecting, setSelecting] = useState(false);
  const [tick, setTick] = useState(0);

  const [open, setOpen] = useState<string | null>(null);
  const [detail, setDetail] = useState<CommitDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [copied, setCopied] = useState<string | null>(null);

  const searchRef = useRef<HTMLInputElement>(null);
  const authorKeys = filter.authorKeys;
  const authorParam = useMemo(() => authorKeys.join(","), [authorKeys]);
  const selected = useMemo(() => new Set(filter.hashes), [filter.hashes]);

  useEffect(() => {
    const t = window.setTimeout(() => setDebounced(query.trim()), 250);
    return () => window.clearTimeout(t);
  }, [query]);

  useEffect(() => {
    searchRef.current?.focus();
  }, []);

  useEffect(() => {
    setPage(0);
  }, [debounced, order, authorParam]);

  const fetchKey = `${repoId}|${debounced}|${authorParam}|${order}|${page}|${refreshTick}|${tick}`;
  useEffect(() => {
    let cancel = false;
    setLoading(true);
    api
      .commits(repoId, {
        q: debounced || undefined,
        author_keys: authorParam || undefined,
        order,
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fetchKey]);

  useEffect(() => {
    if (!open) {
      setDetail(null);
      return;
    }
    let cancel = false;
    setDetailLoading(true);
    api
      .commitDetail(repoId, open)
      .then((d) => {
        if (!cancel) setDetail(d);
      })
      .catch(() => {
        if (!cancel) setDetail(null);
      })
      .finally(() => {
        if (!cancel) setDetailLoading(false);
      });
    return () => {
      cancel = true;
    };
  }, [repoId, open]);

  const rows = data?.rows ?? [];
  const total = data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / PAGE));
  const listMode = filter.mode === "list";

  const applySelection = (next: string[]) =>
    onPatch({ hashes: next, mode: next.length > 0 ? "list" : "all" });

  const toggleOne = (hash: string) => {
    const next = selected.has(hash)
      ? filter.hashes.filter((h) => h !== hash)
      : [...filter.hashes, hash];
    applySelection(next);
  };

  const pageHashes = rows.map((r) => r.hash);
  const pageSet = new Set(pageHashes);
  const allPageSelected = pageHashes.length > 0 && pageHashes.every((h) => selected.has(h));

  const togglePage = () => {
    if (allPageSelected) {
      applySelection(filter.hashes.filter((h) => !pageSet.has(h)));
    } else {
      applySelection([...new Set([...filter.hashes, ...pageHashes])]);
    }
  };

  const selectAllMatching = async () => {
    setSelecting(true);
    setNote(null);
    try {
      const res = await api.commitHashes(repoId, {
        q: debounced || undefined,
        author_keys: authorParam || undefined,
      });
      applySelection(res.hashes);
      setNote(
        res.truncated
          ? `Selection capped at the newest ${fmtInt(res.hashes.length)} of ${fmtInt(res.total)} matching commits.`
          : `Selected all ${fmtInt(res.hashes.length)} matching commits.`,
      );
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSelecting(false);
    }
  };

  const copyHash = async (hash: string) => {
    try {
      await navigator.clipboard.writeText(hash);
      setCopied(hash);
      window.setTimeout(() => setCopied((c) => (c === hash ? null : c)), 1200);
    } catch {
      /* clipboard unavailable — ignore */
    }
  };

  return (
    <div className="card">
      <div className="card-head">
        <div className="row wrap">
          <input
            ref={searchRef}
            className="input input-inline"
            placeholder="Search subject or hash…  ( / )"
            value={query}
            spellCheck={false}
            data-search="commits"
            onChange={(e) => setQuery(e.target.value)}
          />
          {query && (
            <button className="btn btn-small btn-ghost" onClick={() => setQuery("")}>
              Clear
            </button>
          )}
          <select
            className="input input-inline"
            value={order}
            onChange={(e) => setOrder(e.target.value as "asc" | "desc")}
            title="Commit order"
          >
            <option value="desc">Newest first</option>
            <option value="asc">Oldest first</option>
          </select>
        </div>
        <div className="row">
          {loading && data && <Spinner />}
          <span className="muted">
            {fmtInt(total)} non-merge commits reachable from HEAD
            {authorKeys.length > 0 && " · filtered by the selected authors"}
          </span>
        </div>
      </div>

      {error && <ErrorBanner message={error} onRetry={() => setTick((t) => t + 1)} />}
      {note && <div className="banner mt">{note}</div>}

      {listMode && (
        <div className="sel-bar">
          <span className="pill pill-accent">{fmtInt(filter.hashes.length)} commits selected</span>
          <span className="muted">
            Commit-list mode is active — every view now reflects only these commits.
          </span>
          <span className="spacer" />
          <button
            className="btn btn-small btn-ghost"
            onClick={() => onPatch({ mode: "all", hashes: [] })}
          >
            Back to all time
          </button>
        </div>
      )}

      {!data ? (
        <Spinner label="Loading commits…" />
      ) : rows.length === 0 ? (
        <Empty
          title="No commits match"
          hint={debounced ? `Nothing matches “${debounced}”.` : "Try clearing the filters."}
        />
      ) : (
        <>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th className="sel-cell">
                    <input
                      type="checkbox"
                      checked={allPageSelected}
                      onChange={togglePage}
                      title="Select every commit on this page"
                    />
                  </th>
                  <th>Commit</th>
                  <th>Date</th>
                  <th>Author</th>
                  <th>Subject</th>
                  <th className="num">+</th>
                  <th className="num">−</th>
                  <th className="num">Files</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <Fragment key={r.hash}>
                    <tr className={selected.has(r.hash) ? "row-selected" : ""}>
                      <td className="sel-cell">
                        <input
                          type="checkbox"
                          checked={selected.has(r.hash)}
                          onChange={() => toggleOne(r.hash)}
                        />
                      </td>
                      <td>
                        <button
                          className="link-button mono"
                          title={`${r.hash} — click to copy`}
                          onClick={() => copyHash(r.hash)}
                        >
                          {copied === r.hash ? "copied" : r.short}
                        </button>
                      </td>
                      <td className="muted nw">{fmtDateTime(r.ts)}</td>
                      <td title={r.author_email}>{r.author_label}</td>
                      <td>
                        <button
                          className="subject-button"
                          title="Show the changed files"
                          onClick={() => setOpen((o) => (o === r.hash ? null : r.hash))}
                        >
                          {r.subject || "(no subject)"}
                        </button>
                      </td>
                      <td className="num good">+{fmtInt(r.added)}</td>
                      <td className="num bad">−{fmtInt(r.removed)}</td>
                      <td className="num muted">{fmtInt(r.file_count)}</td>
                    </tr>
                    {open === r.hash && (
                      <tr className="detail-row">
                        <td colSpan={8}>
                          {detailLoading && !detail ? (
                            <Spinner label="Loading commit…" />
                          ) : detail ? (
                            <div className="commit-detail">
                              <div className="muted">
                                {detail.author.label} · {detail.author.name} &lt;
                                {detail.author.email}&gt;
                                {detail.author.canonical_email !== detail.author.email &&
                                  ` → ${detail.author.canonical_email}`}
                                {detail.commit.parent_hash && (
                                  <>
                                    {" · parent "}
                                    <span className="mono">
                                      {detail.commit.parent_hash.slice(0, 10)}
                                    </span>
                                  </>
                                )}
                              </div>
                              <div className="table-wrap">
                                <table className="table">
                                  <thead>
                                    <tr>
                                      <th>File</th>
                                      <th className="num">+</th>
                                      <th className="num">−</th>
                                      <th />
                                    </tr>
                                  </thead>
                                  <tbody>
                                    {detail.files.map((f, i) => (
                                      <tr key={`${f.path}-${i}`}>
                                        <td className="path-cell">
                                          {f.is_rename && f.old_path && (
                                            <span className="muted mono">{f.old_path} → </span>
                                          )}
                                          {f.path}
                                        </td>
                                        <td className="num">{f.binary ? "—" : fmtInt(f.added)}</td>
                                        <td className="num">{f.binary ? "—" : fmtInt(f.removed)}</td>
                                        <td>
                                          {f.binary && <span className="marker">binary</span>}
                                          {f.is_rename && <span className="marker">rename</span>}
                                        </td>
                                      </tr>
                                    ))}
                                  </tbody>
                                </table>
                              </div>
                            </div>
                          ) : (
                            <span className="muted">Could not load this commit.</span>
                          )}
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>

          <div className="pager">
            <span className="row wrap">
              <button
                className="btn btn-small"
                disabled={selecting}
                onClick={selectAllMatching}
                title="Select every commit matching the current search / author filters"
              >
                {selecting ? "Selecting…" : `Select all ${fmtInt(total)} matching`}
              </button>
              {filter.hashes.length > 0 && (
                <button
                  className="btn btn-small btn-ghost"
                  onClick={() => onPatch({ mode: "all", hashes: [] })}
                >
                  Clear selection
                </button>
              )}
            </span>
            <span className="row">
              <span className="muted">
                {fmtInt(page * PAGE + 1)}–{fmtInt(Math.min((page + 1) * PAGE, total))} of{" "}
                {fmtInt(total)}
              </span>
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
