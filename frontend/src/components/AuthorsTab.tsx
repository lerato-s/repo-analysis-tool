import { useEffect, useMemo, useState } from "react";
import { api, type AuthorGroup, type AuthorsPayload } from "../api";
import { Empty, ErrorBanner, fmtDate, fmtInt, fmtPct, Spinner } from "../ui";

const MAILMAP_NOTE: Record<string, string> = {
  worktree: "Identities are canonicalised with the repository's .mailmap (worktree).",
  "head-blob":
    "Identities are canonicalised with the .mailmap stored in the analysed ref.",
  none: "This repository has no .mailmap, so identities are grouped by exact name + email. Merge related identities manually below.",
};

/** Author identity merging: mailmap groups + manual merge operations. */
export default function AuthorsTab({
  repoId,
  refreshTick,
  onDataChanged,
}: {
  repoId: number;
  refreshTick: number;
  onDataChanged: () => void;
}) {
  const [payload, setPayload] = useState<AuthorsPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const [query, setQuery] = useState("");

  const [checked, setChecked] = useState<number[]>([]);
  const [label, setLabel] = useState("");
  const [labelEdited, setLabelEdited] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancel = false;
    api
      .authors(repoId)
      .then((p) => {
        if (!cancel) {
          setPayload(p);
          setError(null);
        }
      })
      .catch((err) => {
        if (!cancel) setError((err as Error).message);
      });
    return () => {
      cancel = true;
    };
  }, [repoId, refreshTick, tick]);

  const groups = payload?.groups ?? [];
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return groups;
    return groups.filter(
      (g) =>
        g.label.toLowerCase().includes(q) ||
        g.emails.some((e) => e.toLowerCase().includes(q)) ||
        g.aliases.some((a) => a.name.toLowerCase().includes(q)),
    );
  }, [groups, query]);

  const byKey = useMemo(() => new Map(groups.map((g) => [g.key, g])), [groups]);
  const selected = useMemo(
    () =>
      checked
        .map((k) => byKey.get(k))
        .filter((g): g is AuthorGroup => g !== undefined),
    [checked, byKey],
  );

  // Prefill the merged label from the selection until the user edits it.
  useEffect(() => {
    if (labelEdited) return;
    const first = selected[0];
    setLabel(first ? first.label : "");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [checked, labelEdited]);

  const toggle = (key: number) =>
    setChecked((c) => (c.includes(key) ? c.filter((k) => k !== key) : [...c, key]));

  const merge = async () => {
    if (selected.length < 2) return;
    const members: [string, string][] = selected.flatMap((g) =>
      g.aliases.map((a) => [a.name, a.email] as [string, string]),
    );
    setBusy(true);
    try {
      const res = await api.mergeAuthors(repoId, label.trim() || selected[0].label, members);
      setPayload(res);
      setChecked([]);
      setLabel("");
      setLabelEdited(false);
      setError(null);
      onDataChanged();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const undo = async (opId: number) => {
    setBusy(true);
    try {
      const res = await api.deleteMergeOp(repoId, opId);
      setPayload(res);
      setError(null);
      onDataChanged();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  if (error && !payload) {
    return <ErrorBanner message={error} onRetry={() => setTick((t) => t + 1)} />;
  }
  if (!payload) {
    return (
      <div className="card">
        <Spinner label="Loading authors…" />
      </div>
    );
  }

  const mailmapNote = MAILMAP_NOTE[payload.mailmap_kind ?? "none"] ?? "";
  const totalChurn = groups.reduce((s, g) => s + g.churn, 0);

  return (
    <div className="card">
      <div className="card-head">
        <div className="row wrap">
          <input
            className="input input-inline"
            placeholder="Search name or email…  ( / )"
            value={query}
            spellCheck={false}
            data-search="authors"
            onChange={(e) => setQuery(e.target.value)}
          />
          {query && (
            <button className="btn btn-small btn-ghost" onClick={() => setQuery("")}>
              Clear
            </button>
          )}
        </div>
        <span className="muted">
          {fmtInt(groups.length)} author groups from {fmtInt(payload.identity_count)} git
          identities
        </span>
      </div>

      {mailmapNote && <div className="banner">{mailmapNote}</div>}
      {error && <ErrorBanner message={error} onDismiss={() => setError(null)} />}

      {selected.length >= 2 && (
        <div className="sel-bar">
          <span className="pill pill-accent">{selected.length} groups selected</span>
          <span className="muted">
            {fmtInt(
              selected.reduce((s, g) => s + g.aliases.length, 0),
            )}{" "}
            identities will merge into one author.
          </span>
          <input
            className="input input-inline"
            placeholder="Merged author name"
            value={label}
            onChange={(e) => {
              setLabel(e.target.value);
              setLabelEdited(true);
            }}
          />
          <button className="btn btn-small btn-primary" disabled={busy} onClick={merge}>
            {busy ? "Merging…" : "Merge identities"}
          </button>
          <button
            className="btn btn-small btn-ghost"
            onClick={() => {
              setChecked([]);
              setLabel("");
              setLabelEdited(false);
            }}
          >
            Cancel
          </button>
        </div>
      )}

      {filtered.length === 0 ? (
        <Empty title="No authors match" hint={`Nothing matches “${query}”.`} />
      ) : (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th className="sel-cell" title="Select two or more groups to merge them" />
                <th>Author</th>
                <th>Identities</th>
                <th className="num">Commits</th>
                <th className="num">Churn</th>
                <th className="num">Ownership</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((g) => (
                <tr key={g.key} className={checked.includes(g.key) ? "row-selected" : ""}>
                  <td className="sel-cell">
                    <input
                      type="checkbox"
                      checked={checked.includes(g.key)}
                      onChange={() => toggle(g.key)}
                    />
                  </td>
                  <td>
                    <div>{g.label}</div>
                    <div className="muted mono ellipsis" title={g.emails.join(", ")}>
                      {g.emails.slice(0, 3).join(", ")}
                      {g.emails.length > 3 && ` +${g.emails.length - 3}`}
                    </div>
                  </td>
                  <td>
                    <span className="muted">
                      {fmtInt(g.aliases.length)} identity
                      {g.aliases.length === 1 ? "" : "ies"}
                    </span>
                    {g.aliases.length > 1 && (
                      <div
                        className="muted mono ellipsis"
                        title={g.aliases.map((a) => `${a.name} <${a.email}>`).join("\n")}
                      >
                        {g.aliases
                          .slice(0, 2)
                          .map((a) => `${a.name} <${a.email}>`)
                          .join(" · ")}
                        {g.aliases.length > 2 && ` · +${g.aliases.length - 2}`}
                      </div>
                    )}
                  </td>
                  <td className="num">{fmtInt(g.commits)}</td>
                  <td className="num">{fmtInt(g.churn)}</td>
                  <td className="num">
                    <span className="own">
                      <span className="own-bar">
                        <span
                          style={{ width: `${totalChurn ? (g.churn / totalChurn) * 100 : 0}%` }}
                        />
                      </span>
                      {fmtPct(g.ownership, 1)}
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {payload.ops.length > 0 && (
        <div className="mt">
          <div className="card-head">
            <h3 className="card-title">Manual merges</h3>
            <span className="card-sub">undoing restores the original identities</span>
          </div>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Merged author</th>
                  <th>Identities</th>
                  <th>Created</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {payload.ops.map((op) => (
                  <tr key={op.id}>
                    <td>{op.label || "(unnamed)"}</td>
                    <td className="muted mono ellipsis" title={op.members.map((m) => `${m[0]} <${m[1]}>`).join("\n")}>
                      {op.members.length} identities
                    </td>
                    <td className="muted nw">{fmtDate(op.created_ts)}</td>
                    <td className="num">
                      <button
                        className="btn btn-small btn-danger"
                        disabled={busy}
                        onClick={() => undo(op.id)}
                      >
                        Undo
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
