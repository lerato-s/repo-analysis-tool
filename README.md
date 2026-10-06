# RAT — Repo Analysis Tool

A web dashboard that makes git repositories less opaque: RAT ingests a repository
(zip or clone URL), computes line-level metrics per **commit**, **file**,
**directory**, **commit set** and **author**, and visualises them in a fast,
filterable multi-repo dashboard.

Built for the COMS3011A test brief: ingestion via **zip upload (including `.git`)**
and **remote clone URL**, **author merging** (automatic `.mailmap` + manual merges),
**multi-repo support**, and filtering by **repository / author / file-or-directory /
commit set** (time period or a hand-picked commit list).

## How it works

- **Ingestion** — a single streaming pass of
  `git log --no-merges --numstat -z -M50% -l20000` per repository feeds SQLite.
  Git itself is the reference implementation for rename detection (50 %
  similarity), binary detection and `.mailmap` canonicalisation; line counts come
  from the object database, so the working tree never affects metrics.
- **Metrics engine** — aggregates are computed once per
  `(repo, data_version, filter, granularity)` and served from an LRU cache;
  directory metrics roll up the path tree, so scoping to any folder is O(prefix).
- **API** — FastAPI + SQLite (WAL). **Frontend** — React 18 + TypeScript + Vite +
  ECharts (modular imports, route-level code splitting).

## Features

- **Repository ingestion** — zip upload (archive containing a `.git` dir, nested
  layouts handled) or deep clone of a remote URL; live progress with phases.
- **Multi-repo support** — add, re-analyse and delete repositories from the UI.
- **Dashboard** — KPI cards plus six linked charts: churn timeline, directory
  treemap (click to scope), top files by churn (click to scope), author-ownership
  donut, stacked per-author activity, and a file × time volatility heatmap.
- **Filtering** — repository, author (multi-select), file/directory scope, and
  commit sets: *all time*, *time range* (inclusive start, exclusive end, committer
  dates) or a *manually selected commit list* built in the commit explorer.
- **Files & directories table** — sortable, searchable, paginated metrics with a
  top-author breakdown per row; click a path to scope the whole dashboard to it.
- **Commit explorer** — search by subject/hash, per-commit file detail (renames,
  binaries), and checkbox selection that live-drives the commit-set filter
  ("select all N matching" included).
- **Author merging** — `.mailmap` groups automatically (worktree file or the
  blob in the analysed ref); related identities can be merged manually into a
  labelled group and un-
merged again at any time.

## Metrics

For every commit `h` → previous commit (with 50% rename detection; deletions
count as removed lines on their old path; binaries are not measured):

| Metric | Definition |
| --- | --- |
| Added / Removed | lines added `l⁺` / removed `l⁻` between `h` and `h[p]` |
| Growth | `δ = l⁺ − l⁻` |
| Churn | `λ = l⁺ + l⁻` |

Aggregated over any commit set `H` and object `o` (file or directory subtree):
`l⁺(H,o)`, `l⁻(H,o)`, `δ(H,o)`, `λ(H,o)`, modifications
`n(H,o)` (commits with `λ > 0`), modification frequency `η = n/|H|`, churn rate
`ρ = λ/|H|`, and per author: modifications, churn, and **ownership**
`ω = λ(H,o,a) / λ(H,o)`. Commit sets are the non-merge commits reachable from
`HEAD`, by committer date.

## Quick start

Prerequisites: **git**, **Python 3.11+**, **Node 18+**.

```bash
# 1 — backend dependencies
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt

# 2 — build the frontend (the backend serves frontend/dist)
cd frontend
npm install
npm run build
cd ..

# 3 — run
cd backend
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open **http://127.0.0.1:8000**, add a repository (zip or clone URL) and explore.
Data lives in `./data` by default; delete a repository in the UI to remove it.

### Development mode

```bash
cd backend  && uvicorn app.main:app --port 8000        # API on :8000
cd frontend && npm run dev                             # Vite on :5173, proxies /api → :8000
```

### Tests

```bash
cd backend && python -m pytest        # 74 tests — correctness of every metric,
                                      # API smoke tests; isolated RAT_DATA_DIR sandbox
```

## Configuration (environment variables)

| Variable | Default | Purpose |
| --- | --- | --- |
| `RAT_DATA_DIR` | `./data` | SQLite DB + cloned/extracted repositories |
| `RAT_DB_PATH` | `$RAT_DATA_DIR/rat.db` | Override just the database location |
| `RAT_MAX_UPLOAD_MB` | `4096` | Zip upload size limit |
| `RAT_CLONE_TIMEOUT_S` | `3600` | Clone worker timeout |
| `RAT_ANALYZE_TIMEOUT_S` | `7200` | Analysis worker timeout |

## API overview

All under `/api` — see `/docs` for the interactive OpenAPI schema.

| Endpoint | Purpose |
| --- | --- |
| `POST /repos/upload` / `POST /repos/clone` | Ingest a zip / clone a URL |
| `GET /repos` · `GET /repos/{id}` · `DELETE /repos/{id}` | Manage repositories |
| `POST /repos/{id}/reanalyze` | Re-run analysis (quick or `?full=true`) |
| `POST /repos/{id}/analytics` | KPIs, charts, top objects for a filter body |
| `POST /repos/{id}/objects` | Files/directories table (sort, search, paging) |
| `GET /repos/{id}/commits` · `/commits/hashes` · `/commits/{hash}` | Commit explorer |
| `GET /repos/{id}/authors` · `POST /authors/merge` · `DELETE /authors/ops/{id}` | Author merging |

Scale limits kept deliberately visible in the UI: charts show the top 500 author
groups / 4 000 directories, manual commit lists cap at 100 000 hashes, table pages
at 500 rows.

## Performance

Measured on the validation repositories: **cJSON** (955 commits), **redis**
(11 874), **git** (~61 000 commits, 7 482 objects, 2 481 author groups). Full
ingestion: 0.3 s / 9 s / 34 s. Analytics at 61 k commits: ~0.6 s cold, ~40 ms
cached; author-filtered and range queries ≈ 0.2–0.4 s. Metric correctness was
cross-checked against an independently implemented parser on all three
repositories.

## Project layout

```
backend/
  app/
    ingest/        zip/clone sources, git-log streaming parser, analyzer
    metrics/       filter specs, aggregation engine, caches, author grouping
    routers/       repos, analytics/objects/commits, authors
    db.py          SQLite schema + helpers
  tests/           fixture repositories + metric/API test suites
frontend/
  src/
    pages/         Repos (ingestion) · RepoDashboard (shell)
    components/    FilterBar, DashboardTab, ObjectsTab, CommitsTab, AuthorsTab
    charts.ts      ECharts option builders        api.ts — typed API client
```

## AI usage declaration

This project was developed with AI coding assistance (code generation, review and
testing) under human direction, per the course's AI policy.