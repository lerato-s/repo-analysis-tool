"""Repository management: list, upload (zip), clone (URL), re-analyse, delete."""
from __future__ import annotations

import zipfile
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from .. import config, db
from ..ingest import sources, worker
from ..metrics import engine
from ..schemas import CloneRequest
from .deps import get_repo, remove_owned, unique_dir, unique_upload

router = APIRouter(prefix="/api/repos", tags=["repositories"])

_ACTIVE = ("pending", "cloning", "extracting", "analyzing")
_CHUNK = 1 << 20  # 1 MiB


def repo_json(row: dict) -> dict:
    origin = row["origin"]
    return {
        "id": row["id"],
        "name": row["name"],
        "kind": row["kind"],
        "origin": origin,
        "origin_label": Path(origin).name if row["kind"] == "zip" else origin,
        "status": row["status"],
        "phase": row["phase"],
        "progress": round(row["progress"] or 0.0, 4),
        "error": row["error"],
        "branch": row["branch"],
        "head_hash": row["head_hash"],
        "commit_count": row["commit_count"],
        "change_count": row["change_count"],
        "file_count": row["file_count"],
        "binary_count": row["binary_count"],
        "obj_count": row["obj_count"],
        "first_commit_ts": row["first_commit_ts"],
        "last_commit_ts": row["last_commit_ts"],
        "mailmap_kind": row["mailmap_kind"],
        "created_ts": row["created_ts"],
        "ready_ts": row["ready_ts"],
        "data_version": row["data_version"],
    }


def _reject_active(repo: dict, action: str) -> None:
    if worker.is_running(repo["id"]) or repo["status"] in _ACTIVE:
        raise HTTPException(
            status_code=409,
            detail=f"Ingestion is still running for “{repo['name']}”; wait for it "
            f"to finish before you {action}.",
        )


@router.get("")
def list_repos() -> dict:
    conn = db.connect()
    rows = db.fetch_dicts(conn, "SELECT * FROM repos ORDER BY created_ts DESC, id DESC")
    return {"repos": [repo_json(r) for r in rows]}


@router.post("/upload", status_code=201)
def upload_repo(
    file: UploadFile = File(...),
    name: str | None = Form(default=None),
) -> dict:
    """Accept a zip archive of a repository (must contain .git)."""
    conn = db.connect()
    if not sources.free_space_ok():
        raise HTTPException(
            status_code=400,
            detail="Not enough free disk space to import another repository.",
        )
    base = (name or "").strip() or Path(file.filename or "repo").stem or "repo"
    zip_path = unique_upload(base)
    total = 0
    try:
        with zip_path.open("wb") as out:
            while True:
                chunk = file.file.read(_CHUNK)
                if not chunk:
                    break
                total += len(chunk)
                if total > config.MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"Archive exceeds the {config.MAX_UPLOAD_BYTES // (1 << 20)} MB "
                        "upload limit.",
                    )
                out.write(chunk)
    except Exception:
        zip_path.unlink(missing_ok=True)
        raise
    if total == 0:
        zip_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")
    if not zipfile.is_zipfile(zip_path):
        zip_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=400,
            detail="That file is not a valid .zip archive.",
        )

    repo_name = sources.unique_name(conn, sources.slugify(base, "repo"))
    dest = unique_dir(repo_name)
    cur = conn.execute(
        "INSERT INTO repos(name,kind,origin,dir,created_ts) VALUES (?,?,?,?,?)",
        (repo_name, "zip", str(zip_path), str(dest), sources.timestamp()),
    )
    conn.commit()
    repo_id = cur.lastrowid
    worker.submit(repo_id, full=True)
    return repo_json(get_repo(repo_id))


@router.post("/clone", status_code=201)
def clone_repo(body: CloneRequest) -> dict:
    """Deep-clone a remote repository and analyse it."""
    conn = db.connect()
    try:
        url = sources.validate_url(body.url)
    except sources.SourceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not sources.free_space_ok():
        raise HTTPException(
            status_code=400,
            detail="Not enough free disk space to clone another repository.",
        )
    base = (body.name or "").strip() or sources.name_from_url(url)
    repo_name = sources.unique_name(conn, sources.slugify(base, "repo"))
    dest = unique_dir(repo_name)
    cur = conn.execute(
        "INSERT INTO repos(name,kind,origin,dir,created_ts) VALUES (?,?,?,?,?)",
        (repo_name, "clone", url, str(dest), sources.timestamp()),
    )
    conn.commit()
    repo_id = cur.lastrowid
    worker.submit(repo_id, full=True)
    return repo_json(get_repo(repo_id))


@router.get("/{repo_id}")
def get_repo_detail(repo_id: int) -> dict:
    return repo_json(get_repo(repo_id))


@router.post("/{repo_id}/reanalyze", status_code=202)
def reanalyze(repo_id: int, full: bool = False) -> dict:
    """Re-run the analysis. ``full=true`` also re-fetches the sources
    (re-clone / re-extract), picking up new commits."""
    repo = get_repo(repo_id)
    _reject_active(repo, "re-analyse it")
    if not worker.submit(repo_id, full=full):
        raise HTTPException(status_code=409, detail="Ingestion is already queued for this repository.")
    # Flip the status right away so the UI starts polling and concurrent
    # delete/re-analyse requests are rejected until the worker finishes.
    conn = db.connect()
    conn.execute(
        "UPDATE repos SET status='analyzing', phase='queued for re-analysis',"
        " progress=0.02, error=NULL WHERE id=?",
        (repo_id,),
    )
    conn.commit()
    engine.invalidate(repo_id)
    return {"ok": True, "full": full}


@router.delete("/{repo_id}")
def delete_repo(repo_id: int) -> dict:
    repo = get_repo(repo_id)
    _reject_active(repo, "delete it")
    conn = db.connect()
    conn.execute("DELETE FROM repos WHERE id=?", (repo_id,))
    conn.commit()
    remove_owned(repo["dir"])
    if repo["kind"] == "zip":
        remove_owned(repo["origin"])
    engine.invalidate(repo_id)
    return {"ok": True}
