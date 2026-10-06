"""Central configuration for the Repo Analysis Tool (RAT) backend."""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("RAT_DATA_DIR", str(BASE_DIR / "data"))).resolve()
REPOS_DIR = DATA_DIR / "repos"
TMP_DIR = DATA_DIR / "tmp"
# Uploaded zips are kept so a full re-analysis can re-extract without re-upload.
UPLOADS_DIR = DATA_DIR / "uploads"
DB_PATH = Path(os.environ.get("RAT_DB_PATH", str(DATA_DIR / "rat.db"))).resolve()
FRONTEND_DIST = BASE_DIR / "frontend" / "dist"

# ---------------------------------------------------------------------------
# Git behaviour
# ---------------------------------------------------------------------------
# Rename detection threshold required by the spec (50%).
RENAME_THRESHOLD = "50%"
# Safety cap for the number of rename candidates git considers per commit.
# Commits touching more files than this fall back to git's own warning path.
RENAME_LIMIT = 20_000
CLONE_TIMEOUT_S = int(os.environ.get("RAT_CLONE_TIMEOUT_S", "3600"))
ANALYZE_TIMEOUT_S = int(os.environ.get("RAT_ANALYZE_TIMEOUT_S", "7200"))
# Only the first-parent chain's default branch (HEAD) is analysed.
ANALYZE_REF = "HEAD"

# ---------------------------------------------------------------------------
# Uploads / limits
# ---------------------------------------------------------------------------
MAX_UPLOAD_BYTES = int(os.environ.get("RAT_MAX_UPLOAD_MB", "4096")) * 1024 * 1024
MAX_TREE_NODES = 50_000
MAX_LIST_HASHES = 100_000

# ---------------------------------------------------------------------------
# Analytics cache
# ---------------------------------------------------------------------------
ANALYTICS_CACHE_SIZE = 48
