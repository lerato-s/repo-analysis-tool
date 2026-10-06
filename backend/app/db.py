"""SQLite storage layer for RAT.

One database file holds every repository's parsed history so that metric
queries are pure SQL (fast, no per-request git invocations).

Schema overview
---------------
repos          one row per ingested repository (status/progress/stats)
authors        raw git identities (name, email) + mailmap-canonical identity
commits        non-merge commits reachable from the analysed ref
changes        one row per (commit, changed path) with line counts; renames
               are attributed to the *new* path, binaries carry added=removed=0
repo_paths     every path ever seen in the repo (for pickers / binary+deleted flags)
author_merge_ops  manual author merge operations; members are stored as
               [name, email] identity pairs (not row ids) so they stay valid
               when a repository is re-analysed
"""
from __future__ import annotations

import sqlite3
import threading

from . import config

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS repos (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  kind TEXT NOT NULL,
  origin TEXT NOT NULL,
  dir TEXT NOT NULL,
  branch TEXT,
  head_hash TEXT,
  status TEXT NOT NULL DEFAULT 'pending',
  phase TEXT NOT NULL DEFAULT '',
  error TEXT,
  progress REAL NOT NULL DEFAULT 0,
  created_ts INTEGER NOT NULL,
  ready_ts INTEGER,
  commit_count INTEGER NOT NULL DEFAULT 0,
  file_count INTEGER NOT NULL DEFAULT 0,
  change_count INTEGER NOT NULL DEFAULT 0,
  binary_count INTEGER NOT NULL DEFAULT 0,
  obj_count INTEGER NOT NULL DEFAULT 0,
  first_commit_ts INTEGER,
  last_commit_ts INTEGER,
  mailmap_kind TEXT,
  data_version INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS authors (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  repo_id INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  email TEXT NOT NULL,
  canonical_name TEXT NOT NULL,
  canonical_email TEXT NOT NULL,
  commit_count INTEGER NOT NULL DEFAULT 0,
  churn INTEGER NOT NULL DEFAULT 0,
  UNIQUE(repo_id, name, email)
);
CREATE INDEX IF NOT EXISTS idx_authors_repo ON authors(repo_id);

CREATE TABLE IF NOT EXISTS commits (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  repo_id INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
  hash TEXT NOT NULL,
  ts INTEGER NOT NULL,
  author_id INTEGER NOT NULL REFERENCES authors(id),
  parent_hash TEXT,
  subject TEXT NOT NULL DEFAULT '',
  added INTEGER NOT NULL DEFAULT 0,
  removed INTEGER NOT NULL DEFAULT 0,
  file_count INTEGER NOT NULL DEFAULT 0,
  UNIQUE(repo_id, hash)
);
CREATE INDEX IF NOT EXISTS idx_commits_repo_ts ON commits(repo_id, ts DESC);
CREATE INDEX IF NOT EXISTS idx_commits_repo_author ON commits(repo_id, author_id);

CREATE TABLE IF NOT EXISTS changes (
  repo_id INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
  commit_id INTEGER NOT NULL REFERENCES commits(id) ON DELETE CASCADE,
  path TEXT NOT NULL,
  old_path TEXT,
  ts INTEGER NOT NULL,
  author_id INTEGER NOT NULL,
  added INTEGER NOT NULL,
  removed INTEGER NOT NULL,
  binary INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_changes_repo_path ON changes(repo_id, path);
CREATE INDEX IF NOT EXISTS idx_changes_repo_commit ON changes(repo_id, commit_id);
CREATE INDEX IF NOT EXISTS idx_changes_repo_ts ON changes(repo_id, ts);

CREATE TABLE IF NOT EXISTS repo_paths (
  repo_id INTEGER NOT NULL,
  path TEXT NOT NULL,
  in_head INTEGER NOT NULL DEFAULT 0,
  ever_binary INTEGER NOT NULL DEFAULT 0,
  is_gitlink INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (repo_id, path)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS author_merge_ops (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  repo_id INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
  label TEXT NOT NULL,
  members TEXT NOT NULL,
  created_ts INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_merge_ops_repo ON author_merge_ops(repo_id);
"""


def _configure(conn: sqlite3.Connection) -> None:
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=15000")
    conn.execute("PRAGMA synchronous=NORMAL")


def connect() -> sqlite3.Connection:
    """Return a thread-local connection (created on first use)."""
    conn: sqlite3.Connection | None = getattr(_local, "conn", None)
    if conn is None:
        config.DATA_DIR.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(config.DB_PATH), timeout=30)
        _configure(conn)
        _local.conn = conn
    return conn


def init_db() -> None:
    config.REPOS_DIR.mkdir(parents=True, exist_ok=True)
    config.TMP_DIR.mkdir(parents=True, exist_ok=True)
    config.UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    conn = connect()
    conn.executescript(SCHEMA)
    conn.commit()


def fetch_dicts(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def fetch_one(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> dict | None:
    row = conn.execute(sql, params).fetchone()
    return dict(row) if row else None


def reset_interrupted() -> None:
    """Repos stuck mid-ingestion because the server restarted."""
    conn = connect()
    conn.execute(
        "UPDATE repos SET status='error', phase='interrupted',"
        " error='Ingestion was interrupted by a server restart. "
        "Use “Re-analyse” to run it again.'"
        " WHERE status IN ('pending','cloning','extracting','analyzing')"
    )
    conn.commit()
