import { useEffect, useMemo, useState } from "react";
import type { AuthorGroup } from "../api";
import {
  inputToTs,
  tsToInput,
  type FilterState,
} from "../filters";
import { fmtInt } from "../ui";

const PICKER_VISIBLE = 200;

/** Commit-set, author and object-scope filters shared by every tab. */
export default function FilterBar({
  state,
  onChange,
  authors,
  dirs,
  files,
  onOpenCommits,
}: {
  state: FilterState;
  onChange: (patch: Partial<FilterState>) => void;
  authors: AuthorGroup[] | null;
  dirs: string[];
  files: string[];
  onOpenCommits: () => void;
}) {
  const [pathDraft, setPathDraft] = useState(state.path);
  const [authorQuery, setAuthorQuery] = useState("");

  useEffect(() => setPathDraft(state.path), [state.path]);

  const selectedAuthors = useMemo(() => {
    if (!authors) return [] as AuthorGroup[];
    const byKey = new Map(authors.map((g) => [g.key, g]));
    return state.authorKeys.map((k) => byKey.get(k)).filter(Boolean) as AuthorGroup[];
  }, [authors, state.authorKeys]);

  const filteredAuthors = useMemo(() => {
    const q = authorQuery.trim().toLowerCase();
    const list = authors ?? [];
    const matched = q
      ? list.filter(
          (g) =>
            g.label.toLowerCase().includes(q) ||
            g.emails.some((e) => e.toLowerCase().includes(q)),
        )
      : list;
    return matched.slice(0, PICKER_VISIBLE);
  }, [authors, authorQuery]);

  const applyPath = (value: string) => {
    const next = value.trim().replace(/^\/+|\/+$/g, "");
    onChange({ path: next });
  };

  return (
    <div className="card filters">
      <div className="row wrap">
        <div className="seg" role="tablist" aria-label="Commit set">
          <button
            className={state.mode === "all" ? "active" : ""}
            onClick={() => onChange({ mode: "all" })}
          >
            All time
          </button>
          <button
            className={state.mode === "range" ? "active" : ""}
            onClick={() => onChange({ mode: "range" })}
          >
            Time range
          </button>
          <button
            className={state.mode === "list" ? "active" : ""}
            onClick={() => onChange({ mode: "list" })}
          >
            Commit list
          </button>
        </div>

        <span className="spacer" />

        <div className="row">
          <label className="inline-label" htmlFor="scope-input">
            Scope
          </label>
          <input
            id="scope-input"
            className="input input-inline mono"
            list="scope-options"
            placeholder="whole repository"
            value={pathDraft}
            spellCheck={false}
            onChange={(e) => setPathDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") applyPath(pathDraft);
              if (e.key === "Escape") setPathDraft(state.path);
            }}
            onBlur={() => {
              if (pathDraft !== state.path) applyPath(pathDraft);
            }}
          />
          {state.path && (
            <button
              className="chip chip-button"
              title="Clear scope"
              onClick={() => onChange({ path: "" })}
            >
              {state.path} ×
            </button>
          )}
        </div>

        <div className="row">
          <label className="inline-label" htmlFor="granularity">
            Bucket
          </label>
          <select
            id="granularity"
            className="input input-inline"
            value={state.granularity}
            onChange={(e) =>
              onChange({ granularity: e.target.value as FilterState["granularity"] })
            }
          >
            <option value="auto">auto</option>
            <option value="day">day</option>
            <option value="week">week</option>
            <option value="month">month</option>
          </select>
        </div>
      </div>

      {state.mode === "range" && (
        <div className="row wrap mt">
          <label className="inline-label">From</label>
          <input
            type="datetime-local"
            className="input input-inline"
            value={tsToInput(state.fromTs)}
            onChange={(e) => onChange({ fromTs: inputToTs(e.target.value) })}
          />
          <label className="inline-label">until (excl.)</label>
          <input
            type="datetime-local"
            className="input input-inline"
            value={tsToInput(state.toTs)}
            onChange={(e) => onChange({ toTs: inputToTs(e.target.value) })}
          />
          <span className="muted">
            empty bounds mean “from the first commit” / “until now”
          </span>
        </div>
      )}

      {state.mode === "list" && (
        <div className="row wrap mt">
          <span className="pill pill-accent">
            {fmtInt(state.hashes.length)} commit{state.hashes.length === 1 ? "" : "s"} selected
          </span>
          {state.hashes.length === 0 && (
            <span className="muted">
              Pick commits in the Commits tab to build a custom commit set.
            </span>
          )}
          <span className="spacer" />
          <button className="btn btn-small" onClick={onOpenCommits}>
            Choose commits
          </button>
          {state.hashes.length > 0 && (
            <button
              className="btn btn-small btn-ghost"
              onClick={() => onChange({ hashes: [] })}
            >
              Clear list
            </button>
          )}
        </div>
      )}

      <div className="row wrap mt">
        <label className="inline-label">Authors</label>
        <details className="picker">
          <summary>
            {state.authorKeys.length === 0
              ? "All authors"
              : `${state.authorKeys.length} selected`}
          </summary>
          <div className="picker-body">
            <input
              className="input"
              placeholder="Search by name or email…"
              value={authorQuery}
              onChange={(e) => setAuthorQuery(e.target.value)}
            />
            {authors === null ? (
              <div className="muted" style={{ padding: 10 }}>
                Loading authors…
              </div>
            ) : (
              <>
                <div className="picker-list">
                  {filteredAuthors.map((g) => (
                    <label key={g.key} className="picker-row">
                      <input
                        type="checkbox"
                        checked={state.authorKeys.includes(g.key)}
                        onChange={(e) => {
                          const next = e.target.checked
                            ? [...state.authorKeys, g.key]
                            : state.authorKeys.filter((k) => k !== g.key);
                          onChange({ authorKeys: next });
                        }}
                      />
                      <span className="picker-label" title={g.emails.join(", ")}>
                        {g.label || g.emails[0]}
                      </span>
                      <span className="muted">{fmtInt(g.commits)} commits</span>
                    </label>
                  ))}
                </div>
                {authors.length > PICKER_VISIBLE && !authorQuery && (
                  <div className="muted picker-note">
                    Showing the top {PICKER_VISIBLE} of {fmtInt(authors.length)} — search to
                    find more.
                  </div>
                )}
              </>
            )}
            {state.authorKeys.length > 0 && (
              <div className="picker-note">
                <button className="btn btn-small btn-ghost" onClick={() => onChange({ authorKeys: [] })}>
                  Clear selection
                </button>
              </div>
            )}
          </div>
        </details>
        {selectedAuthors.slice(0, 5).map((g) => (
          <button
            key={g.key}
            className="chip chip-button"
            title="Remove author filter"
            onClick={() =>
              onChange({ authorKeys: state.authorKeys.filter((k) => k !== g.key) })
            }
          >
            {g.label} ×
          </button>
        ))}
        {selectedAuthors.length > 5 && (
          <span className="muted">+{selectedAuthors.length - 5} more</span>
        )}
      </div>

      <datalist id="scope-options">
        {dirs.map((d) => (
          <option key={d} value={d} />
        ))}
        {files.map((f) => (
          <option key={f} value={f} />
        ))}
      </datalist>
    </div>
  );
}
