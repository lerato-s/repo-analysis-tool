"""Parse a local git repository into the SQLite store in a single pass.

Pipeline
--------
1. ``git rev-list --count --no-merges HEAD`` for the progress denominator.
2. One streamed ``git log --no-merges --numstat -z -M50% -l20000
   --format=%x1e…`` invocation, parsed record-by-record (see ``gitlog``).
3. Bulk inserts into ``commits`` / ``changes`` / ``authors`` / ``repo_paths``.

Using git's own diff machinery means rename detection (50% similarity),
binary detection and ``.mailmap`` resolution match the spec exactly — git
*is* the reference implementation for all three. Line counts come from the
object database, so the working tree state never affects metrics.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

from .. import config, db
from . import gitcmd, gitlog

ProgressFn = Callable[[float, str], None]

_LOG_FORMAT = "%x1e%H%x1f%an%x1f%ae%x1f%aN%x1f%aE%x1f%ct%x1f%P%x1f%s"
_BATCH_COMMITS = 500  # progress + transaction granularity


def analyze_repository(
    repo_id: int, repo_dir: Path, progress: ProgressFn | None = None
) -> dict:
    conn = db.connect()
    repo_dir = Path(repo_dir)
    notify = progress or (lambda frac, msg: None)

    # Re-analysis is idempotent: wipe this repo's parsed history first.
    for table in ("changes", "commits", "authors", "repo_paths"):
        conn.execute(f"DELETE FROM {table} WHERE repo_id=?", (repo_id,))
    conn.commit()

    mailmap_kind = _ensure_mailmap(repo_dir)
    head_hash = _git_str(repo_dir, ["rev-parse", config.ANALYZE_REF])
    branch_proc = gitcmd.run_git(
        repo_dir, ["symbolic-ref", "--quiet", "--short", config.ANALYZE_REF], check=False
    )
    branch = branch_proc.stdout.decode("utf-8", "replace").strip() or "(detached)"
    total = int(
        _git_str(repo_dir, ["rev-list", "--count", "--no-merges", config.ANALYZE_REF])
    )
    conn.execute(
        "UPDATE repos SET head_hash=?, branch=?, mailmap_kind=?, error=NULL WHERE id=?",
        (head_hash, branch, mailmap_kind, repo_id),
    )
    conn.commit()

    args = [
        "log",
        "--no-merges",
        "--numstat",
        "-z",
        f"-M{config.RENAME_THRESHOLD}",
        f"-l{config.RENAME_LIMIT}",
        f"--format={_LOG_FORMAT}",
        config.ANALYZE_REF,
    ]

    author_cache: dict[tuple[str, str], int] = {}
    author_commits: dict[int, int] = {}
    author_churn: dict[int, int] = {}
    path_flags: dict[str, list[int]] = {}  # path -> [in_head, ever_binary, gitlink]
    change_rows: list[tuple] = []
    parsed = 0
    change_total = 0
    binary_total = 0
    ts_min: int | None = None
    ts_max: int | None = None
    last_notify = 0.0

    def flush_changes() -> None:
        nonlocal change_rows
        if change_rows:
            conn.executemany(
                "INSERT INTO changes(repo_id,commit_id,path,old_path,ts,author_id,"
                "added,removed,binary) VALUES (?,?,?,?,?,?,?,?,?)",
                change_rows,
            )
            change_rows = []

    def flags_for(path: str) -> list[int]:
        flags = path_flags.get(path)
        if flags is None:
            flags = path_flags[path] = [0, 0, 0]
        return flags

    with gitcmd.stream_git(
        repo_dir, args, timeout=config.ANALYZE_TIMEOUT_S
    ) as stream:
        for header, changes in gitlog.parse_log_stream(stream.stdout):
            key = (header.author_name, header.author_email)
            aid = author_cache.get(key)
            if aid is None:
                conn.execute(
                    "INSERT OR IGNORE INTO authors(repo_id,name,email,"
                    "canonical_name,canonical_email) VALUES (?,?,?,?,?)",
                    (
                        repo_id,
                        header.author_name,
                        header.author_email,
                        header.canonical_name,
                        header.canonical_email,
                    ),
                )
                row = conn.execute(
                    "SELECT id FROM authors WHERE repo_id=? AND name=? AND email=?",
                    (repo_id, header.author_name, header.author_email),
                ).fetchone()
                aid = int(row["id"])
                author_cache[key] = aid
            author_commits[aid] = author_commits.get(aid, 0) + 1

            c_added = 0
            c_removed = 0
            for ch in changes:
                c_added += ch.added
                c_removed += ch.removed
                flags_for(ch.path)  # every changed path is a known object
                if ch.old_path:
                    # The pre-rename path is also part of h[F]/h[p][F] unions
                    # (it keeps its own pre-rename metric history).
                    old_flags = flags_for(ch.old_path)
                    if ch.binary:
                        old_flags[1] = 1
                if ch.binary:
                    flags_for(ch.path)[1] = 1
                    binary_total += 1
                author_churn[aid] = author_churn.get(aid, 0) + ch.added + ch.removed

            cur = conn.execute(
                "INSERT INTO commits(repo_id,hash,ts,author_id,parent_hash,subject,"
                "added,removed,file_count) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    repo_id,
                    header.hash,
                    header.ts,
                    aid,
                    header.parent,
                    header.subject,
                    c_added,
                    c_removed,
                    len(changes),
                ),
            )
            commit_id = cur.lastrowid
            for ch in changes:
                change_rows.append(
                    (
                        repo_id,
                        commit_id,
                        ch.path,
                        ch.old_path,
                        header.ts,
                        aid,
                        ch.added,
                        ch.removed,
                        1 if ch.binary else 0,
                    )
                )

            change_total += len(changes)
            parsed += 1
            if ts_min is None or header.ts < ts_min:
                ts_min = header.ts
            if ts_max is None or header.ts > ts_max:
                ts_max = header.ts

            if parsed % _BATCH_COMMITS == 0:
                flush_changes()
                conn.commit()
                now = time.time()
                if now - last_notify > 0.4:
                    last_notify = now
                    frac = parsed / total if total else 0.0
                    notify(frac, f"Parsing history ({parsed:,}/{total:,} commits)")
        stream.wait()
    flush_changes()
    conn.commit()

    # Author roll-ups.
    author_ids = set(author_commits) | set(author_churn)
    conn.executemany(
        "UPDATE authors SET commit_count=?, churn=? WHERE id=?",
        [(author_commits.get(a, 0), author_churn.get(a, 0), a) for a in author_ids],
    )

    # Path inventory: everything from changes + the tree at the analysed ref.
    head_entries = _ls_tree(repo_dir)
    for path, is_gitlink in head_entries:
        flags = flags_for(path)
        flags[0] = 1
        if is_gitlink:
            flags[2] = 1
    conn.executemany(
        "INSERT OR REPLACE INTO repo_paths(repo_id,path,in_head,ever_binary,is_gitlink) "
        "VALUES (?,?,?,?,?)",
        [(repo_id, p, f[0], f[1], f[2]) for p, f in path_flags.items()],
    )

    stats = {
        "commit_count": parsed,
        "change_count": change_total,
        "binary_count": binary_total,
        "obj_count": len(path_flags),
        "file_count": len(head_entries),
        "first_commit_ts": ts_min,
        "last_commit_ts": ts_max,
    }
    conn.execute(
        "UPDATE repos SET commit_count=?, change_count=?, binary_count=?, obj_count=?,"
        " file_count=?, first_commit_ts=?, last_commit_ts=?,"
        " data_version = data_version + 1 WHERE id=?",
        (
            stats["commit_count"],
            stats["change_count"],
            stats["binary_count"],
            stats["obj_count"],
            stats["file_count"],
            stats["first_commit_ts"],
            stats["last_commit_ts"],
            repo_id,
        ),
    )
    conn.commit()
    notify(1.0, f"Analysed {parsed:,} commits / {change_total:,} file changes")
    return stats


def _git_str(repo_dir: Path, args: list[str]) -> str:
    return gitcmd.run_git(repo_dir, args).stdout.decode("utf-8", "replace").strip()


def _ensure_mailmap(repo_dir: Path) -> str:
    """Guarantee git can resolve ``.mailmap`` identities, even for bare zips.

    Returns where the mailmap came from: ``worktree``, ``head-blob`` or ``none``.
    """
    if (repo_dir / ".mailmap").is_file():
        return "worktree"
    probe = gitcmd.run_git(
        repo_dir, ["cat-file", "-e", f"{config.ANALYZE_REF}:.mailmap"], check=False
    )
    if probe.returncode == 0:
        # Bare / worktree-less repositories: point git at the blob in HEAD.
        gitcmd.run_git(
            repo_dir, ["config", "mailmap.blob", f"{config.ANALYZE_REF}:.mailmap"]
        )
        return "head-blob"
    return "none"


def _ls_tree(repo_dir: Path) -> list[tuple[str, bool]]:
    """All entries at the analysed ref: ``(path, is_gitlink)``."""
    proc = gitcmd.run_git(
        repo_dir, ["ls-tree", "-r", "-z", "--full-tree", config.ANALYZE_REF]
    )
    entries: list[tuple[str, bool]] = []
    for tok in proc.stdout.split(b"\x00"):
        if not tok:
            continue
        meta, _, path = tok.partition(b"\t")
        if not path:
            continue
        parts = meta.split(b" ")
        is_gitlink = len(parts) >= 2 and parts[1] == b"commit"
        entries.append((path.decode("utf-8", "replace"), is_gitlink))
    return entries
