"""Dashboard data: metric aggregates, object tables, commit explorer."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import config, db
from ..metrics import engine
from ..schemas import FilterBody
from .deps import build_spec, get_ready_repo
from .repos import repo_json

router = APIRouter(prefix="/api/repos/{repo_id}", tags=["analytics"])

DIRS_CAP = 4000  # treemap nodes returned per response
AUTHORS_CAP = 500
TOP_FILES = 15
PAGE_MAX = 500


def _agg_or_400(repo, conn, body: FilterBody):
    spec, groups = build_spec(conn, repo, body)
    agg = engine.compute_cached(conn, repo, spec, granularity=body.granularity)
    if "error" in agg:
        raise HTTPException(status_code=400, detail=agg["error"])
    return spec, groups, agg


# ---------------------------------------------------------------------------
# Aggregated dashboard payload
# ---------------------------------------------------------------------------
@router.post("/analytics")
def analytics(repo_id: int, body: FilterBody) -> dict:
    repo = get_ready_repo(repo_id)
    conn = db.connect()
    _, _, agg = _agg_or_400(repo, conn, body)

    dirs = sorted(agg["dirs"].values(), key=lambda d: -d["churn"])
    top_files = [f for f in sorted(agg["files"].values(), key=lambda f: -f["churn"]) if f["churn"] > 0][
        :TOP_FILES
    ]
    return {
        "repo": repo_json(repo),
        "filter": {
            "mode": body.mode,
            "from_ts": body.from_ts,
            "to_ts": body.to_ts,
            "author_keys": body.author_keys,
            "path": agg["scope"]["path"],
            "hashes_count": len(body.hashes),
        },
        "kpis": {
            "n_commits": agg["n_commits"],
            "object": agg["object"],
            "repo_totals": agg["repo_totals"],
            "file_count": len(agg["files"]),
            "dir_count": len(agg["dirs"]),
            "author_count": len(agg["authors"]),
            "first_ts": agg["first_ts"],
            "last_ts": agg["last_ts"],
        },
        "authors": agg["authors"][:AUTHORS_CAP],
        "dirs": dirs[:DIRS_CAP],
        "top_files": top_files,
        "timeline": agg["timeline"],
        "author_timeline": agg["author_timeline"],
        "heatmap": agg["heatmap"],
        "truncated": agg["truncated"],
    }


# ---------------------------------------------------------------------------
# Object tables (files / directories)
# ---------------------------------------------------------------------------
@router.post("/objects")
def objects(
    repo_id: int,
    body: FilterBody,
    kind: str = "file",
    q: str = "",
    sort: str = "churn",
    order: str = "desc",
    offset: int = 0,
    limit: int = 50,
) -> dict:
    repo = get_ready_repo(repo_id)
    conn = db.connect()
    spec, groups, agg = _agg_or_400(repo, conn, body)
    kind = "dir" if kind == "dir" else "file"
    limit = max(1, min(limit, PAGE_MAX))
    offset = max(0, offset)
    page = engine.object_rows(
        agg, kind, q=q or None, sort=sort, order=order, offset=offset, limit=limit
    )
    paths = [r["path"] for r in page["rows"]]
    if kind == "dir":
        tops = engine.page_top_authors(conn, repo, spec, groups, dirs=paths)
    else:
        tops = engine.page_top_authors(conn, repo, spec, groups, paths=paths)
    for r in page["rows"]:
        r["tops"] = tops.get(r["path"], [])
    return {"kind": kind, "total": page["total"], "rows": page["rows"]}


# ---------------------------------------------------------------------------
# Commit explorer + manual commit selection
# ---------------------------------------------------------------------------
def _like(q: str) -> str:
    return q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _commit_filters(conn, repo: dict, q: str, author_keys: str):
    """WHERE fragment + params for the commit list. None ids => no author filter,
    empty list => deliberately empty result."""
    clauses = ["c.repo_id = ?"]
    params: list = [repo["id"]]
    q = (q or "").strip()
    if q:
        pat = f"%{_like(q)}%"
        clauses.append("(c.subject LIKE ? ESCAPE '\\' OR c.hash LIKE ? ESCAPE '\\')")
        params.extend([pat, f"{_like(q)}%"])
    keys = []
    for part in (author_keys or "").split(","):
        part = part.strip()
        if part.lstrip("-").isdigit():
            keys.append(int(part))
    if keys:
        groups = engine.author_group_map(conn, repo)
        ids: list[int] = []
        for key in keys:
            g = groups["by_key"].get(key)
            if g is not None:
                ids.extend(g["member_ids"])
        if not ids:
            ids = [-1]
        clauses.append(f"c.author_id IN ({','.join('?' * len(ids))})")
        params.extend(ids)
    return " AND ".join(clauses), params


@router.get("/commits")
def list_commits(
    repo_id: int,
    q: str = "",
    author_keys: str = "",
    offset: int = 0,
    limit: int = 50,
    order: str = "desc",
) -> dict:
    repo = get_ready_repo(repo_id)
    conn = db.connect()
    where, params = _commit_filters(conn, repo, q, author_keys)
    total = conn.execute(
        f"SELECT COUNT(*) n FROM commits c WHERE {where}", params
    ).fetchone()["n"]
    limit = max(1, min(limit, PAGE_MAX))
    offset = max(0, offset)
    direction = "ASC" if order == "asc" else "DESC"
    rows = conn.execute(
        "SELECT c.hash, c.ts, c.subject, c.added, c.removed, c.file_count,"
        " c.parent_hash, c.author_id, a.name author_name, a.email author_email"
        " FROM commits c JOIN authors a ON a.id = c.author_id"
        f" WHERE {where} ORDER BY c.ts {direction}, c.id {direction} LIMIT ? OFFSET ?",
        [*params, limit, offset],
    ).fetchall()
    groups = engine.author_group_map(conn, repo)
    out = []
    for r in rows:
        g = groups["by_key"].get(groups["raw_to_key"].get(r["author_id"]))
        out.append(
            {
                "hash": r["hash"],
                "short": r["hash"][:10],
                "ts": r["ts"],
                "subject": r["subject"],
                "added": r["added"],
                "removed": r["removed"],
                "file_count": r["file_count"],
                "parent_hash": r["parent_hash"],
                "author_name": r["author_name"],
                "author_email": r["author_email"],
                "author_label": g["label"] if g else r["author_name"],
            }
        )
    return {"total": total, "rows": out}


@router.get("/commits/hashes")
def commit_hashes(
    repo_id: int,
    q: str = "",
    author_keys: str = "",
    cap: int | None = None,
) -> dict:
    """All hashes matching the search — used for 'select all matching'."""
    repo = get_ready_repo(repo_id)
    conn = db.connect()
    where, params = _commit_filters(conn, repo, q, author_keys)
    limit = min(cap or config.MAX_LIST_HASHES, config.MAX_LIST_HASHES)
    rows = conn.execute(
        f"SELECT c.hash FROM commits c WHERE {where} ORDER BY c.ts DESC, c.id DESC LIMIT ?",
        [*params, limit],
    ).fetchall()
    total = conn.execute(
        f"SELECT COUNT(*) n FROM commits c WHERE {where}", params
    ).fetchone()["n"]
    return {
        "hashes": [r["hash"] for r in rows],
        "total": total,
        "truncated": total > len(rows),
    }


@router.get("/commits/{hash}")
def commit_detail(repo_id: int, hash: str) -> dict:
    repo = get_ready_repo(repo_id)
    conn = db.connect()
    row = db.fetch_one(
        conn, "SELECT * FROM commits WHERE repo_id=? AND hash=?", (repo_id, hash)
    )
    if row is None and len(hash) >= 4:
        row = db.fetch_one(
            conn,
            "SELECT * FROM commits WHERE repo_id=? AND hash LIKE ? || '%'"
            " ORDER BY ts DESC LIMIT 1",
            (repo_id, hash),
        )
    if row is None:
        raise HTTPException(status_code=404, detail="Commit not found in this repository.")
    author = db.fetch_one(conn, "SELECT * FROM authors WHERE id=?", (row["author_id"],))
    groups = engine.author_group_map(conn, repo)
    g = groups["by_key"].get(groups["raw_to_key"].get(row["author_id"]))
    files = db.fetch_dicts(
        conn,
        "SELECT path, old_path, added, removed, binary FROM changes"
        " WHERE commit_id=? ORDER BY path",
        (row["id"],),
    )
    for f in files:
        f["binary"] = bool(f["binary"])
        f["is_rename"] = bool(f["old_path"]) and f["old_path"] != f["path"]
    return {
        "commit": {
            "hash": row["hash"],
            "short": row["hash"][:10],
            "ts": row["ts"],
            "subject": row["subject"],
            "parent_hash": row["parent_hash"],
            "added": row["added"],
            "removed": row["removed"],
            "file_count": row["file_count"],
        },
        "author": {
            "label": g["label"] if g else author["canonical_name"],
            "name": author["name"],
            "email": author["email"],
            "canonical_name": author["canonical_name"],
            "canonical_email": author["canonical_email"],
        },
        "files": files,
    }
