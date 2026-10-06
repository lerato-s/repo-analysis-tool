"""Background ingestion jobs: clone / extract / analyse, with live progress.

Jobs run on a small thread pool; progress and status are written back to the
``repos`` row so the UI can poll ``GET /api/repos/{id}``.
"""
from __future__ import annotations

import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .. import config, db
from . import analyzer, sources

_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="rat-ingest")
_running: set[int] = set()
_running_lock = threading.Lock()


def _install_root(repo_dir: Path) -> Path:
    """Top-level folder under ``REPOS_DIR`` that holds a checkout.

    A zip may nest the repository inside the upload root (``<slug>/wrapper/del``),
    and the row's ``dir`` is that *nested* path. Re-installing must target the
    top-level folder (``<slug>``): moving a new extraction into the nested path
    would add one wrapper level on every full re-analysis.
    """
    p = Path(repo_dir)
    try:
        rel = p.resolve().relative_to(config.REPOS_DIR.resolve())
    except (ValueError, OSError):
        return p
    return config.REPOS_DIR / rel.parts[0] if rel.parts else p


def _update(repo_id: int, **fields) -> None:
    conn = db.connect()
    cols = ", ".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE repos SET {cols} WHERE id=?", (*fields.values(), repo_id))
    conn.commit()


def _progress(repo_id: int, base: float, span: float):
    """Throttled progress reporter mapping a phased callback into 0..1."""
    state = {"last": 0.0}

    def update(frac: float, message: str) -> None:
        frac = max(0.0, min(1.0, frac))
        now = time.time()
        if frac < 1.0 and now - state["last"] < 0.4:
            return
        state["last"] = now
        _update(repo_id, progress=base + span * frac, phase=message[:300])

    return update


def submit(repo_id: int, *, full: bool) -> bool:
    """Queue an ingestion job. ``full`` also (re-)fetches the sources."""
    with _running_lock:
        if repo_id in _running:
            return False
        _running.add(repo_id)
    _executor.submit(_guarded, repo_id, full)
    return True


def is_running(repo_id: int) -> bool:
    with _running_lock:
        return repo_id in _running


def _guarded(repo_id: int, full: bool) -> None:
    try:
        _run(repo_id, full)
    except Exception as exc:  # surfaced to the UI as a friendly error
        message = str(exc).strip() or exc.__class__.__name__
        _update(repo_id, status="error", phase="", progress=0.0, error=message[:2000])
    finally:
        with _running_lock:
            _running.discard(repo_id)


def _run(repo_id: int, full: bool) -> None:
    conn = db.connect()
    repo = db.fetch_one(conn, "SELECT * FROM repos WHERE id=?", (repo_id,))
    if repo is None:
        return

    dest = Path(repo["dir"])
    repo_dir = dest
    created_here = False
    installed_ok = False
    tmp_root: Path | None = None
    try:
        if full and repo["kind"] == "clone":
            _update(
                repo_id,
                status="cloning",
                phase="cloning repository",
                progress=0.0,
                error=None,
            )
            if dest.exists():
                shutil.rmtree(dest)
            sources.clone_repository(
                repo["origin"], dest, progress=_progress(repo_id, 0.0, 0.4)
            )
            created_here = True
        elif full:  # zip upload
            _update(
                repo_id,
                status="extracting",
                phase="extracting archive",
                progress=0.0,
                error=None,
            )
            zip_path = Path(repo["origin"])
            if not zip_path.is_file():
                raise sources.SourceError(
                    "The uploaded archive is no longer on disk — please re-upload it."
                )
            tmp_root = sources.extract_zip(
                zip_path, progress=_progress(repo_id, 0.0, 0.35)
            )
            repo_root, top_dir = sources.locate_repo_root(tmp_root)
            dest = _install_root(dest)
            if dest.exists():
                shutil.rmtree(dest)
            repo_dir = sources.install_extracted(top_dir, repo_root, dest)
            created_here = True

        # A zip may nest the repo, so persist the resolved dir for later.
        _update(repo_id, dir=str(repo_dir))

        if not repo_dir.exists():
            raise sources.SourceError(
                "Repository files are missing on disk — delete this entry and add it again."
            )
        sources.validate_repo(repo_dir)
        installed_ok = True

        _update(
            repo_id,
            status="analyzing",
            phase="analysing history",
            progress=0.4,
            error=None,
        )
        analyzer.analyze_repository(
            repo_id, repo_dir, progress=_progress(repo_id, 0.4, 0.6)
        )
        _update(
            repo_id,
            status="ready",
            phase="",
            progress=1.0,
            error=None,
            ready_ts=int(time.time()),
        )
    except Exception:
        # A broken clone/extraction should not be kept around.
        if created_here and not installed_ok and dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        raise
    finally:
        if tmp_root is not None and tmp_root.exists():
            sources.cleanup(tmp_root)
