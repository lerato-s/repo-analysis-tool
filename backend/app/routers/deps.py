"""Shared helpers for the API routers."""
from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import HTTPException

from .. import config, db
from ..ingest import sources
from ..metrics import engine
from ..schemas import FilterBody


def get_repo(repo_id: int) -> dict:
    repo = db.fetch_one(db.connect(), "SELECT * FROM repos WHERE id=?", (repo_id,))
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found.")
    return repo


def get_ready_repo(repo_id: int) -> dict:
    repo = get_repo(repo_id)
    if repo["status"] != "ready":
        if repo["error"]:
            raise HTTPException(status_code=409, detail=repo["error"])
        raise HTTPException(
            status_code=409,
            detail=f"Repository is not ready yet (status: {repo['status']}).",
        )
    return repo


def build_spec(conn, repo_row: dict, body: FilterBody):
    """Translate an API filter body into an engine FilterSpec (plus the
    effective-author group map, reused by callers for labels)."""
    groups = engine.author_group_map(conn, repo_row)
    ids: list[int] = []
    for key in body.author_keys:
        g = groups["by_key"].get(key)
        if g is not None:
            ids.extend(g["member_ids"])
    if body.author_keys and not ids:
        ids = [-1]  # stale keys -> deliberately empty result set
    spec = engine.FilterSpec(
        mode=body.mode,
        from_ts=body.from_ts,
        to_ts=body.to_ts,
        hashes=tuple(body.hashes),
        author_ids=tuple(sorted(set(ids))),
        path=body.path or "",
    )
    return spec, groups


def unique_dir(base: str) -> Path:
    """A free directory under REPOS_DIR for a new repository checkout."""
    slug = sources.slugify(base, "repo")
    candidate = config.REPOS_DIR / slug
    n = 2
    while candidate.exists():
        candidate = config.REPOS_DIR / f"{slug}-{n}"
        n += 1
    return candidate


def unique_upload(base: str) -> Path:
    """A free file under UPLOADS_DIR for an uploaded archive."""
    slug = sources.slugify(base, "upload")
    candidate = config.UPLOADS_DIR / f"{slug}.zip"
    n = 2
    while candidate.exists():
        candidate = config.UPLOADS_DIR / f"{slug}-{n}.zip"
        n += 1
    return candidate


def remove_owned(path_str: str | None) -> None:
    """Delete a file/dir only when it lives inside the app's data directory,
    then prune any *wrapper* directories it leaves empty (e.g. the folder a
    zip was extracted into). Structural dirs (repos/, uploads/, tmp/) are
    never removed."""
    if not path_str:
        return
    structural = {config.DATA_DIR, config.REPOS_DIR, config.UPLOADS_DIR, config.TMP_DIR}
    p = Path(path_str)
    try:
        p = p.resolve()
        p.relative_to(config.DATA_DIR)
    except (ValueError, OSError):
        return
    if p.is_dir():
        shutil.rmtree(p, ignore_errors=True)
    elif p.exists():
        p.unlink(missing_ok=True)
    for parent in p.parents:
        if parent in structural or not parent.is_dir():
            break
        try:
            parent.rmdir()  # only succeeds while empty; never touches data
        except OSError:
            break
