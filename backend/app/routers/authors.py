"""Effective authors: mailmap-based groups plus manual merge operations."""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException

from .. import db
from ..ingest import sources
from ..metrics import engine
from ..schemas import MergeRequest
from .deps import get_repo

router = APIRouter(prefix="/api/repos/{repo_id}/authors", tags=["authors"])


def authors_payload(conn, repo: dict) -> dict:
    """All effective author groups (all-time) + recorded merge operations."""
    groups = engine.author_group_map(conn, repo)
    churn_by_key = dict(_group_churn_values(conn, repo, groups))
    total_churn = sum(churn_by_key.values())
    rows = []
    for g in groups["groups"]:
        churn = churn_by_key.get(g["key"], 0)
        rows.append(
            {
                "key": g["key"],
                "label": g["label"],
                "emails": g["emails"],
                "aliases": g["aliases"],
                "commits": g["commits"],
                "churn": churn,
                "ownership": round(churn / total_churn, 6) if total_churn else 0.0,
            }
        )
    rows.sort(key=lambda r: (-r["churn"], -r["commits"]))
    ops = db.fetch_dicts(
        conn,
        "SELECT id, label, members, created_ts FROM author_merge_ops"
        " WHERE repo_id=? ORDER BY created_ts, id",
        (repo["id"],),
    )
    for op in ops:
        try:
            op["members"] = [list(m) for m in json.loads(op["members"])]
        except (TypeError, ValueError):
            op["members"] = []
    return {
        "mailmap_kind": repo["mailmap_kind"],
        "groups": rows,
        "identity_count": sum(len(g["aliases"]) for g in rows),
        "ops": ops,
    }


def _group_churn_values(conn, repo: dict, groups: dict):
    """(key, churn) per effective group from the raw authors table."""
    raw = {
        r["id"]: r["churn"]
        for r in conn.execute(
            "SELECT id, churn FROM authors WHERE repo_id=?", (repo["id"],)
        )
    }
    for g in groups["groups"]:
        yield g["key"], sum(raw.get(m, 0) for m in g["member_ids"])


@router.get("")
def list_authors(repo_id: int) -> dict:
    repo = get_repo(repo_id)
    return authors_payload(db.connect(), repo)


@router.post("/merge")
def merge_authors(repo_id: int, body: MergeRequest) -> dict:
    repo = get_repo(repo_id)
    conn = db.connect()
    known = {
        (r["name"], r["email"])
        for r in conn.execute(
            "SELECT name, email FROM authors WHERE repo_id=?", (repo_id,)
        )
    }
    members = [[n, e] for n, e in body.members if (n, e) in known]
    if len(members) < 2:
        raise HTTPException(
            status_code=400,
            detail="Select at least two existing author identities to merge.",
        )
    conn.execute(
        "INSERT INTO author_merge_ops(repo_id,label,members,created_ts) VALUES (?,?,?,?)",
        (repo_id, body.label.strip(), json.dumps(members), sources.timestamp()),
    )
    conn.commit()
    engine.invalidate(repo_id)
    return authors_payload(conn, repo)


@router.delete("/ops/{op_id}")
def delete_merge_op(repo_id: int, op_id: int) -> dict:
    repo = get_repo(repo_id)
    conn = db.connect()
    cur = conn.execute(
        "DELETE FROM author_merge_ops WHERE id=? AND repo_id=?", (op_id, repo_id)
    )
    conn.commit()
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="Merge operation not found.")
    engine.invalidate(repo_id)
    return authors_payload(conn, repo)
