"""Metric computation over the parsed history.

All queries run against the SQLite tables populated by ``ingest.analyzer``;
the dashboard never shells out to git, which keeps every interaction fast.

Definitions implemented (h = commit, f = file, d = directory, H = commit set):

* file      ``l+_{h,f}`` / ``l-_{h,f}`` from ``changes`` rows; growth
            ``δ = l+ - l-``; churn ``λ = l+ + l-``. Renames were attributed
            to the new path by the ingest layer; deleted files keep their
            removed lines on their (former) path.
* directory recursive roll-up over descendant files: ``l±_{h,d} = Σ_{f ⊆ d}``
            (equivalent to the spec's immediate-children recursion).
* repository directory metrics on the root ``''``.
* commit set sums ``l±_{H,o}``, modifications ``n_{H,o}`` (commits with
            ``λ_{h,o} > 0``), frequency ``η = n/|H|``, churn rate ``ρ = λ/|H|``.
* author    ``n_{H,o,a}``, ``λ_{H,o,a}``, ownership ``ω = λ_{H,o,a}/λ_{H,o}``
            over *effective* authors (mailmap canonicalisation + manual merges).
"""
from __future__ import annotations

import hashlib
import json
import threading
from collections import Counter, OrderedDict
from dataclasses import dataclass
from typing import Any, Iterator

from .. import config, db

_ROUND = 6


# ---------------------------------------------------------------------------
# Filters
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class FilterSpec:
    mode: str = "all"  # 'all' | 'range' | 'list'
    from_ts: int | None = None  # inclusive
    to_ts: int | None = None  # exclusive
    hashes: tuple[str, ...] = ()  # manual commit selection (mode='list')
    author_ids: tuple[int, ...] = ()  # raw author ids (expanded groups)
    path: str | None = None  # object scope (file or directory)

    def digest(self) -> tuple:
        h = ""
        if self.hashes:
            h = hashlib.sha1("\n".join(self.hashes).encode("utf-8", "replace")).hexdigest()
        return (self.mode, self.from_ts, self.to_ts, self.author_ids, self.path or "", h)


# ---------------------------------------------------------------------------
# Scope + author grouping helpers
# ---------------------------------------------------------------------------
def resolve_scope(conn, repo_id: int, path: str | None) -> dict:
    norm = (path or "").strip().strip("/")
    if not norm:
        return {"path": "", "kind": "root"}
    lo, hi = _dir_range(norm)
    row = conn.execute(
        "SELECT 1 FROM repo_paths WHERE repo_id=? AND path >= ? AND path < ? LIMIT 1",
        (repo_id, lo, hi),
    ).fetchone()
    if row:
        return {"path": norm, "kind": "dir"}
    row = conn.execute(
        "SELECT 1 FROM repo_paths WHERE repo_id=? AND path = ? LIMIT 1",
        (repo_id, norm),
    ).fetchone()
    if row:
        return {"path": norm, "kind": "file"}
    return {"path": norm, "kind": "unknown"}


def _dir_range(prefix: str) -> tuple[str, str]:
    """Index-friendly range for 'all paths strictly under prefix/'."""
    lo = prefix + "/"
    hi = prefix + chr(ord("/") + 1)  # '/' + 1 == '0'
    return lo, hi


def _ancestors(path: str) -> Iterator[str]:
    """Directories containing ``path``, innermost first, root '' last."""
    idx = path.rfind("/")
    while idx != -1:
        yield path[:idx]
        idx = path.rfind("/", 0, idx)
    yield ""


def author_group_map(conn, repo_row) -> dict:
    """Effective author groups: mailmap canonicalisation + manual merges.

    Default grouping key is the lower-cased *canonical* email (mailmap's
    identity key); manual merge ops union raw identities, and their label is
    used as the display name. Stored by [name, email] pairs so re-analysis
    (which re-creates author rows) never invalidates merges.
    """
    repo_id = repo_row["id"]
    authors = db.fetch_dicts(
        conn,
        "SELECT id, name, email, canonical_name, canonical_email, commit_count"
        " FROM authors WHERE repo_id=?",
        (repo_id,),
    )
    ops = db.fetch_dicts(
        conn,
        "SELECT id, label, members, created_ts FROM author_merge_ops"
        " WHERE repo_id=? ORDER BY created_ts, id",
        (repo_id,),
    )

    parent: dict[int, int] = {a["id"]: a["id"] for a in authors}

    def find(x: int) -> int:
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:
            parent[x], x = root, parent[x]
        return root

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    # 1. mailmap canonicalisation groups identities by canonical email.
    by_canon: dict[str, list[int]] = {}
    for a in authors:
        by_canon.setdefault((a["canonical_email"] or a["email"]).lower(), []).append(a["id"])
    for ids in by_canon.values():
        for i in ids[1:]:
            union(ids[0], i)

    # 2. manual merge operations (by identity, not row id).
    by_identity = {(a["name"], a["email"]): a["id"] for a in authors}
    label_assignments: list[tuple[int, str]] = []
    for op in ops:
        try:
            members = [tuple(m) for m in json.loads(op["members"])]
        except (TypeError, ValueError):
            continue
        ids = [by_identity[m] for m in members if m in by_identity]
        if not ids:
            continue
        for i in ids[1:]:
            union(ids[0], i)
        label_assignments.append((ids[0], op["label"]))
    # Resolve labels only after all unions so roots are final.
    labels: dict[int, str] = {}
    for aid, label in label_assignments:
        labels[find(aid)] = label

    groups: dict[int, dict] = {}
    for a in authors:
        root = find(a["id"])
        g = groups.get(root)
        if g is None:
            g = groups[root] = {
                "key": root,
                "label": "",
                "emails": set(),
                "aliases": [],
                "member_ids": [],
                "_names": Counter(),
                "_commits": 0,
            }
        g["member_ids"].append(a["id"])
        g["emails"].add(a["email"])
        g["aliases"].append(
            {"name": a["name"], "email": a["email"], "commits": a["commit_count"]}
        )
        g["_names"][a["canonical_name"] or a["name"] or a["email"]] += a["commit_count"]
        g["_commits"] += a["commit_count"]

    result = []
    for g in groups.values():
        label = labels.get(g["key"]) or (
            g["_names"].most_common(1)[0][0] if g["_names"] else "author"
        )
        result.append(
            {
                "key": g["key"],
                "label": label,
                "emails": sorted(g["emails"]),
                "aliases": sorted(g["aliases"], key=lambda x: -x["commits"]),
                "member_ids": g["member_ids"],
                "commits": g["_commits"],
            }
        )
    result.sort(key=lambda g: -g["commits"])
    return {
        "groups": result,
        "by_key": {g["key"]: g for g in result},
        "raw_to_key": {
            member: g["key"] for g in result for member in g["member_ids"]
        },
    }


# ---------------------------------------------------------------------------
# SQL fragments
# ---------------------------------------------------------------------------
def _prepare_selection(conn, repo_id: int, spec: FilterSpec) -> None:
    """Materialise manually selected commits into a temp table (chunked).

    Note: the temp-table writes must be committed. Python's sqlite3 opens an
    implicit transaction on any DML (temp tables included); leaving it open
    would pin a stale read snapshot on this connection in WAL mode, so later
    reads on the same pooled connection would miss newly committed rows.
    """
    conn.execute("DROP TABLE IF EXISTS temp._sel_ids")
    conn.execute("CREATE TEMP TABLE _sel_ids(commit_id INTEGER PRIMARY KEY)")
    if spec.mode != "list" or not spec.hashes:
        conn.commit()
        return
    hashes = list(spec.hashes[: config.MAX_LIST_HASHES])
    found: list[tuple[int]] = []
    for i in range(0, len(hashes), 500):
        chunk = hashes[i : i + 500]
        ph = ",".join("?" * len(chunk))
        found.extend(
            (row[0],)
            for row in conn.execute(
                f"SELECT id FROM commits WHERE repo_id=? AND hash IN ({ph})",
                (repo_id, *chunk),
            )
        )
    conn.executemany("INSERT OR IGNORE INTO _sel_ids VALUES (?)", found)
    conn.commit()


def _commit_set_where(spec: FilterSpec, repo_id: int) -> tuple[str, list]:
    """WHERE for the `commits` table (alias c) defining the commit set H."""
    clauses = ["c.repo_id = ?"]
    params: list = [repo_id]
    if spec.mode == "range":
        if spec.from_ts is not None:
            clauses.append("c.ts >= ?")
            params.append(spec.from_ts)
        if spec.to_ts is not None:
            clauses.append("c.ts < ?")
            params.append(spec.to_ts)
    elif spec.mode == "list":
        clauses.append("c.id IN (SELECT commit_id FROM _sel_ids)")
    if spec.author_ids:
        clauses.append(f"c.author_id IN ({','.join('?' * len(spec.author_ids))})")
        params.extend(spec.author_ids)
    return " AND ".join(clauses), params


def _changes_where(spec: FilterSpec, repo_id: int, scope: dict) -> tuple[str, list]:
    """WHERE for the `changes` table (alias c): commit set + author + scope."""
    clauses = ["c.repo_id = ?"]
    params: list = [repo_id]
    if spec.mode == "range":
        if spec.from_ts is not None:
            clauses.append("c.ts >= ?")
            params.append(spec.from_ts)
        if spec.to_ts is not None:
            clauses.append("c.ts < ?")
            params.append(spec.to_ts)
    elif spec.mode == "list":
        clauses.append("c.commit_id IN (SELECT commit_id FROM _sel_ids)")
    if spec.author_ids:
        clauses.append(f"c.author_id IN ({','.join('?' * len(spec.author_ids))})")
        params.extend(spec.author_ids)
    kind = scope["kind"]
    if kind == "file":
        clauses.append("c.path = ?")
        params.append(scope["path"])
    elif kind == "dir":
        lo, hi = _dir_range(scope["path"])
        clauses.append("c.path >= ? AND c.path < ?")
        params.extend([lo, hi])
    return " AND ".join(clauses), params


def _bucket_expr(granularity: str) -> str:
    if granularity == "day":
        return "strftime('%Y-%m-%d', c.ts, 'unixepoch')"
    if granularity == "week":
        return "strftime('%Y-W%W', c.ts, 'unixepoch')"
    return "strftime('%Y-%m', c.ts, 'unixepoch')"


def _auto_granularity(first_ts: int | None, last_ts: int | None) -> str:
    if first_ts is None or last_ts is None:
        return "month"
    span_days = max(0, (last_ts - first_ts)) / 86400
    if span_days <= 45:
        return "day"
    if span_days <= 800:
        return "week"
    return "month"


def _m(added: int, removed: int, mods: int, n_commits: int) -> dict:
    return {
        "added": added,
        "removed": removed,
        "growth": added - removed,
        "churn": added + removed,
        "mods": mods,
        "freq": round(mods / n_commits, _ROUND) if n_commits else 0.0,
        "rate": round((added + removed) / n_commits, _ROUND) if n_commits else 0.0,
    }


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------
def compute(conn, repo_row, spec: FilterSpec, *, granularity: str = "auto") -> dict:
    repo_id = repo_row["id"]
    scope = resolve_scope(conn, repo_id, spec.path)
    if scope["kind"] == "unknown":
        return {"error": f"Object not found in this repository: {scope['path']!r}"}

    _prepare_selection(conn, repo_id, spec)
    cw, cp = _commit_set_where(spec, repo_id)
    w, p = _changes_where(spec, repo_id, scope)

    # --- commit set ------------------------------------------------------
    row = conn.execute(
        f"SELECT COUNT(*) n, MIN(c.ts) lo, MAX(c.ts) hi FROM commits c WHERE {cw}", cp
    ).fetchone()
    n_commits = row["n"] or 0
    first_ts, last_ts = row["lo"], row["hi"]

    # --- per-file line sums ---------------------------------------------
    file_totals: dict[str, list[int]] = {}
    for r in conn.execute(
        f"SELECT c.path, SUM(c.added) a, SUM(c.removed) r FROM changes c"
        f" WHERE {w} GROUP BY c.path",
        p,
    ):
        file_totals[r["path"]] = [r["a"], r["r"]]

    # --- modifications: distinct commits per path with churn > 0 ---------
    file_mods: Counter = Counter()
    dir_mods_sets: dict[str, set[int]] = {}
    for r in conn.execute(
        f"SELECT c.commit_id ci, c.path FROM changes c WHERE {w}"
        " GROUP BY c.commit_id, c.path HAVING SUM(c.added) + SUM(c.removed) > 0",
        p,
    ):
        file_mods[r["path"]] += 1
        for anc in _ancestors(r["path"]):
            dir_mods_sets.setdefault(anc, set()).add(r["ci"])

    # --- known paths under scope (inventory incl. zero-metric objects) ---
    scope_cond, scope_params = "", [repo_id]
    if scope["kind"] == "file":
        scope_cond, scope_params = " AND path = ?", [repo_id, scope["path"]]
    elif scope["kind"] == "dir":
        lo, hi = _dir_range(scope["path"])
        scope_cond, scope_params = " AND path >= ? AND path < ?", [repo_id, lo, hi]
    inventory = db.fetch_dicts(
        conn,
        "SELECT path, in_head, ever_binary, is_gitlink FROM repo_paths"
        f" WHERE repo_id=?{scope_cond} ORDER BY path",
        tuple(scope_params),
    )
    truncated = len(inventory) > config.MAX_TREE_NODES
    if truncated:
        inventory = inventory[: config.MAX_TREE_NODES]

    files: dict[str, dict] = {}
    for r in inventory:
        path = r["path"]
        added, removed = file_totals.pop(path, (0, 0))
        files[path] = {
            "path": path,
            "is_file": True,
            "in_head": bool(r["in_head"]),
            "ever_binary": bool(r["ever_binary"]),
            "is_gitlink": bool(r["is_gitlink"]),
            **_m(added, removed, file_mods.get(path, 0), n_commits),
        }
    # Paths with metrics that are somehow absent from the inventory
    # (defensive; e.g. inventory truncation).
    for path, (added, removed) in file_totals.items():
        files[path] = {
            "path": path,
            "is_file": True,
            "in_head": False,
            "ever_binary": False,
            "is_gitlink": False,
            **_m(added, removed, file_mods.get(path, 0), n_commits),
        }

    # --- directory roll-up ------------------------------------------------
    dir_add: Counter = Counter()
    dir_rem: Counter = Counter()
    dir_files: Counter = Counter()
    for path, f in files.items():
        a, r = f["added"], f["removed"]
        n = 1
        for anc in _ancestors(path):
            dir_add[anc] += a
            dir_rem[anc] += r
            dir_files[anc] += n
    dirs: dict[str, dict] = {}
    for d in set(dir_add) | set(dir_rem) | set(dir_files):
        if scope["kind"] == "dir" and d == scope["path"]:
            pass  # scope itself is reported as `object`, still keep for lookup
        dirs[d] = {
            "path": d,
            "is_file": False,
            "file_count": dir_files.get(d, 0),
            **_m(
                dir_add.get(d, 0),
                dir_rem.get(d, 0),
                len(dir_mods_sets.get(d, ())),
                n_commits,
            ),
        }
    # '' root always exists.
    dirs.setdefault(
        "",
        {
            "path": "",
            "is_file": False,
            "file_count": dir_files.get("", 0),
            **_m(
                dir_add.get("", 0),
                dir_rem.get("", 0),
                len(dir_mods_sets.get("", ())),
                n_commits,
            ),
        },
    )

    object_metrics = dirs.get(scope["path"]) if scope["kind"] != "file" else files.get(scope["path"])
    base = {"path": scope["path"], "is_file": scope["kind"] == "file"}
    if object_metrics is None:  # not in inventory (deleted + no metrics)
        object_metrics = {**base, **_m(0, 0, 0, n_commits)}
    else:
        object_metrics = {
            **base,
            **{
                k: object_metrics[k]
                for k in ("added", "removed", "growth", "churn", "mods", "freq", "rate")
            },
        }
        if not object_metrics["is_file"]:
            object_metrics["file_count"] = dirs[scope["path"]]["file_count"]

    # --- repository totals (same filters, no path scope) -----------------
    cw2, cp2 = _changes_where(spec, repo_id, {"path": "", "kind": "root"})
    tot = conn.execute(
        f"SELECT COALESCE(SUM(c.added),0) a, COALESCE(SUM(c.removed),0) r"
        f" FROM changes c WHERE {cw2}",
        cp2,
    ).fetchone()
    repo_totals = {
        "added": tot["a"],
        "removed": tot["r"],
        "growth": tot["a"] - tot["r"],
        "churn": tot["a"] + tot["r"],
    }

    # --- authors ----------------------------------------------------------
    groups = author_group_map(conn, repo_row)
    raw_to_key = groups["raw_to_key"]
    per_author_commits: Counter = Counter()
    for r in conn.execute(
        f"SELECT c.author_id aid, COUNT(*) n FROM commits c WHERE {cw} GROUP BY c.author_id",
        cp,
    ):
        per_author_commits[r["aid"]] = r["n"]
    author_line: Counter = Counter()
    author_added: Counter = Counter()
    author_removed: Counter = Counter()
    for r in conn.execute(
        f"SELECT c.author_id aid, SUM(c.added) a, SUM(c.removed) r FROM changes c"
        f" WHERE {w} GROUP BY c.author_id",
        p,
    ):
        author_added[r["aid"]] += r["a"]
        author_removed[r["aid"]] += r["r"]
        author_line[r["aid"]] += r["a"] + r["r"]
    author_mods_sets: dict[int, set[int]] = {}
    for r in conn.execute(
        f"SELECT c.commit_id ci, c.author_id aid FROM changes c WHERE {w}"
        " GROUP BY c.commit_id, c.author_id HAVING SUM(c.added) + SUM(c.removed) > 0",
        p,
    ):
        author_mods_sets.setdefault(r["aid"], set()).add(r["ci"])

    total_churn = object_metrics["churn"]
    authors: list[dict] = []
    for g in groups["groups"]:
        members = g["member_ids"]
        commits = sum(per_author_commits.get(m, 0) for m in members)
        added = sum(author_added.get(m, 0) for m in members)
        removed = sum(author_removed.get(m, 0) for m in members)
        mods = len(set().union(*(author_mods_sets.get(m, set()) for m in members)))
        churn = added + removed
        if commits == 0 and churn == 0 and mods == 0:
            continue
        authors.append(
            {
                "key": g["key"],
                "label": g["label"],
                "emails": g["emails"],
                "aliases": g["aliases"],
                "commits": commits,
                "added": added,
                "removed": removed,
                "growth": added - removed,
                "churn": churn,
                "mods": mods,
                "ownership": round(churn / total_churn, _ROUND) if total_churn else 0.0,
            }
        )
    authors.sort(key=lambda a: (-a["churn"], -a["commits"]))

    # --- timeline ---------------------------------------------------------
    if granularity not in ("day", "week", "month"):
        granularity = _auto_granularity(first_ts, last_ts)
    bexpr = _bucket_expr(granularity)
    tl: dict[str, dict] = {}
    for r in conn.execute(
        f"SELECT {bexpr} b, SUM(c.added) a, SUM(c.removed) r,"
        " COUNT(DISTINCT c.commit_id) n"
        f" FROM changes c WHERE {w} GROUP BY b ORDER BY b",
        p,
    ):
        tl[r["b"]] = {
            "b": r["b"],
            "added": r["a"],
            "removed": r["r"],
            "growth": r["a"] - r["r"],
            "churn": r["a"] + r["r"],
            "commits": r["n"],
        }
    timeline = {"granularity": granularity, "points": list(tl.values())}

    # --- author-stacked timeline (top authors by scoped churn) -------------
    top_authors = [a for a in authors if a["churn"] > 0][:8]
    author_timeline = {
        "authors": [{"key": a["key"], "label": a["label"]} for a in top_authors],
        "points": [],
    }
    if top_authors:
        top_ids = [m for a in top_authors for m in groups["by_key"][a["key"]]["member_ids"]]
        by_bucket: dict[str, dict[int, int]] = {}
        ph = ",".join("?" * len(top_ids))
        for r in conn.execute(
            f"SELECT {bexpr} b, c.author_id aid, SUM(c.added) + SUM(c.removed) churn"
            f" FROM changes c WHERE {w} AND c.author_id IN ({ph}) GROUP BY b, c.author_id",
            [*p, *top_ids],
        ):
            by_bucket.setdefault(r["b"], {})[r["aid"]] = r["churn"]
        key_of = {a["key"]: i for i, a in enumerate(top_authors)}
        for b in sorted(by_bucket):
            values = [0] * len(top_authors)
            for aid, churn in by_bucket[b].items():
                idx = key_of.get(raw_to_key.get(aid))
                if idx is not None:
                    values[idx] += churn
            author_timeline["points"].append({"b": b, "values": values})

    # --- heatmap: top files x buckets -------------------------------------
    top_files = sorted(files.values(), key=lambda f: -f["churn"])[:12]
    heat = {"buckets": [], "files": [], "values": []}
    heat_files = [f for f in top_files if f["churn"] > 0]
    if heat_files:
        heat["files"] = [f["path"] for f in heat_files]
        idx_of = {f["path"]: i for i, f in enumerate(heat_files)}
        ph = ",".join("?" * len(heat_files))
        cells = conn.execute(
            f"SELECT c.path pth, {bexpr} b, SUM(c.added) + SUM(c.removed) churn"
            f" FROM changes c WHERE {w} AND c.path IN ({ph}) GROUP BY pth, b"
            " HAVING SUM(c.added) + SUM(c.removed) > 0 ORDER BY b",
            [*p, *[f["path"] for f in heat_files]],
        ).fetchall()
        buckets = sorted({r["b"] for r in cells})
        heat["buckets"] = buckets
        b_idx = {b: i for i, b in enumerate(buckets)}
        heat["values"] = [
            [idx_of[r["pth"]], b_idx[r["b"]], r["churn"]] for r in cells
        ]

    return {
        "n_commits": n_commits,
        "first_ts": first_ts,
        "last_ts": last_ts,
        "scope": scope,
        "object": object_metrics,
        "repo_totals": repo_totals,
        "files": files,
        "dirs": dirs,
        "authors": authors,
        "author_groups": {
            "count": len(groups["groups"]),
            "details": [
                {
                    "key": g["key"],
                    "label": g["label"],
                    "emails": g["emails"],
                    "aliases": g["aliases"],
                    "commits": g["commits"],
                }
                for g in groups["groups"]
            ],
        },
        "timeline": timeline,
        "author_timeline": author_timeline,
        "heatmap": heat,
        "truncated": truncated,
    }


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------
_cache: "OrderedDict[tuple, dict]" = OrderedDict()
_cache_lock = threading.Lock()


def compute_cached(conn, repo_row, spec: FilterSpec, *, granularity: str = "auto") -> dict:
    key = (repo_row["id"], repo_row["data_version"], spec.digest(), granularity)
    with _cache_lock:
        hit = _cache.get(key)
        if hit is not None:
            _cache.move_to_end(key)
            return hit
    result = compute(conn, repo_row, spec, granularity=granularity)
    if "error" not in result:
        with _cache_lock:
            _cache[key] = result
            while len(_cache) > config.ANALYTICS_CACHE_SIZE:
                _cache.popitem(last=False)
    return result


def invalidate(repo_id: int | None = None) -> None:
    with _cache_lock:
        if repo_id is None:
            _cache.clear()
        else:
            for key in [k for k in _cache if k[0] == repo_id]:
                _cache.pop(key, None)


# ---------------------------------------------------------------------------
# Object tables (files / directories) + per-page author enrichment
# ---------------------------------------------------------------------------
def object_rows(
    agg: dict,
    kind: str,
    *,
    q: str | None = None,
    sort: str = "churn",
    order: str = "desc",
    offset: int = 0,
    limit: int = 50,
) -> dict:
    scope = agg["scope"]
    scope_path = scope["path"]
    if kind == "file":
        if scope["kind"] == "file":
            rows = [agg["files"][scope_path]] if scope_path in agg["files"] else [agg["object"]]
        else:
            rows = list(agg["files"].values())
    elif scope["kind"] == "file":
        # A file scope has no subdirectories; its metrics live in `object`.
        rows = []
    elif scope_path:
        # Directory scope: only directories *strictly* below it. The ``''``
        # and parent-chain entries are rollups used to derive the scope's own
        # metrics and would otherwise show up (mis-labelled) in the table.
        prefix = scope_path + "/"
        rows = [d for p, d in agg["dirs"].items() if p.startswith(prefix)]
    else:
        rows = [d for p, d in agg["dirs"].items() if p]  # root itself excluded
    if q:
        ql = q.lower()
        rows = [r for r in rows if ql in r["path"].lower()]
    if sort not in ("path", "added", "removed", "growth", "churn", "mods", "freq", "rate", "file_count"):
        sort = "churn"
    reverse = order != "asc"
    rows.sort(key=lambda r: (r["path"] if sort == "path" else r.get(sort, 0)), reverse=reverse)
    total = len(rows)
    return {"total": total, "rows": rows[offset : offset + limit]}


def page_top_authors(
    conn,
    repo_row,
    spec: FilterSpec,
    groups: dict,
    paths: list[str] | None = None,
    dirs: list[str] | None = None,
    *,
    top: int = 2,
) -> dict[str, list[dict]]:
    """Top-N authors (by churn under the current filters) for a page of
    files and/or directories, keyed by object path."""
    paths = list(paths or ())
    dirs = list(dirs or ())
    if not paths and not dirs:
        return {}
    repo_id = repo_row["id"]
    w, p = _changes_where(spec, repo_id, {"path": "", "kind": "root"})
    conds: list[str] = []
    extra: list = []
    if paths:
        conds.append(f"c.path IN ({','.join('?' * len(paths))})")
        extra.extend(paths)
    for d in dirs:
        lo, hi = _dir_range(d)
        conds.append("(c.path >= ? AND c.path < ?)")
        extra.extend([lo, hi])
    per_path: dict[str, Counter] = {}
    for r in conn.execute(
        f"SELECT c.path pth, c.author_id aid, SUM(c.added) + SUM(c.removed) churn"
        f" FROM changes c WHERE {w} AND ({' OR '.join(conds)}) GROUP BY pth, aid",
        [*p, *extra],
    ):
        per_path.setdefault(r["pth"], Counter())[
            groups["raw_to_key"].get(r["aid"], r["aid"])
        ] += r["churn"]
    counters: dict[str, Counter] = {}
    for path in paths:
        if path in per_path:
            counters[path] = per_path[path]
    for d in dirs:
        lo, hi = _dir_range(d)
        merged: Counter = Counter()
        for path, c in per_path.items():
            if lo <= path < hi:
                merged.update(c)
        if merged:
            counters[d] = merged
    out: dict[str, list[dict]] = {}
    for obj, counter in counters.items():
        total = sum(counter.values())
        entries = []
        for key, churn in counter.most_common(top):
            g = groups["by_key"].get(key)
            entries.append(
                {
                    "key": key,
                    "label": g["label"] if g else str(key),
                    "churn": churn,
                    "ownership": round(churn / total, _ROUND) if total else 0.0,
                }
            )
        out[obj] = entries
    return out


def cache_info() -> dict:
    with _cache_lock:
        return {"entries": len(_cache), "size": config.ANALYTICS_CACHE_SIZE}
