"""Metric-correctness tests against the scratch fixture repository.

Ground truth hand-computed from ``git log --no-merges --numstat -M50%`` on
``tests.fixtures.build_scratch_repo`` (and re-verified end-to-end while the
engine was being developed):

  commits (non-merge reachable from HEAD): 13  (incl. chmod-only c6 + empty c11)
  change rows: 16     added 16   removed 2   churn 18
  a.txt             +5 -1  (c1 +3, c2 +2/-1; renamed away in c4)      mods 2
  b.txt             +1 -0  (c4 rename 0/0, c5 +1, c6 chmod 0/0)       mods 1
  bin.dat           binary 0/0 add (c3) + binary delete (c7)          mods 0
  .mailmap          +1 (c8)                                           mods 1
  dir1/one.txt      +2 (c1; moved away in c9)                         mods 1
  dir2/one.txt      +2 (c10; c9 rename 0/0)                           mods 1
  dir2/deep/two.txt +1 -1 (c1; c12 below-50% => delete)               mods 2
  dir2/two.txt      +2 (c12 add)                                      mods 1
  y.txt / z.txt     +1 each (c13)                                     mods 1
  dir1   +2 -0 churn 2 mods 1 (c9 rename 0/0 excluded)
  dir2   +5 -1 churn 6 mods 3 (c1, c10, c12)
  dir2/deep +1 -1 churn 2 mods 2 (c1, c12)
  root   +16 -2 growth 14 churn 18 mods 7; freq 7/13, rate 18/13

Timestamp anchors: 2024-01-0N == 1704067200 + (N-1)*86400; commits land at
10:00:00 UTC (e.g. c5 == 1704448800, c9 == 1704794400).
"""
from __future__ import annotations

import json

import pytest

from app import db
from app.metrics import engine

LINE_KEYS = ("added", "removed", "growth", "churn")
FULL_KEYS = LINE_KEYS + ("mods",)


def metrics_of(m: dict, keys=FULL_KEYS) -> dict:
    return {k: m[k] for k in keys}


def fetch_repo(conn, repo_id: int) -> dict:
    row = db.fetch_one(conn, "SELECT * FROM repos WHERE id=?", (repo_id,))
    assert row is not None
    return row


def aggregate(conn, repo_id: int, **kw) -> dict:
    spec = engine.FilterSpec(**kw)
    result = engine.compute_cached(conn, fetch_repo(conn, repo_id), spec)
    assert "error" not in result, result
    return result


@pytest.fixture(scope="module")
def ctx(scratch_ingested):
    """(connection, repo_id) for the ingested scratch fixture."""
    return db.connect(), scratch_ingested


@pytest.fixture(scope="module")
def agg_all(ctx):
    conn, repo_id = ctx
    return aggregate(conn, repo_id)


# ---------------------------------------------------------------------------
# Ingestion stats
# ---------------------------------------------------------------------------
def test_ingest_stats(ctx):
    conn, repo_id = ctx
    row = fetch_repo(conn, repo_id)
    assert row["commit_count"] == 13
    assert row["change_count"] == 16
    assert row["binary_count"] == 2
    assert row["obj_count"] == 10
    assert row["mailmap_kind"] == "worktree"
    assert row["branch"] == "main"


# ---------------------------------------------------------------------------
# Repository / root metrics
# ---------------------------------------------------------------------------
def test_root_scope_totals(agg_all):
    assert agg_all["n_commits"] == 13
    assert agg_all["scope"] == {"path": "", "kind": "root"}
    assert agg_all["repo_totals"] == {"added": 16, "removed": 2, "growth": 14, "churn": 18}
    root = agg_all["object"]
    assert metrics_of(root) == {"added": 16, "removed": 2, "growth": 14, "churn": 18, "mods": 7}
    assert root["freq"] == round(7 / 13, 6)
    assert root["rate"] == round(18 / 13, 6)
    assert root["file_count"] == 10
    assert root["is_file"] is False


def test_root_directory_matches_repo_totals(agg_all):
    root = agg_all["dirs"][""]
    assert metrics_of(root) == metrics_of(agg_all["object"])


def test_directory_rollup_equals_sum_of_immediate_children(agg_all):
    """Spec invariant: a directory is the recursive sum over its immediate
    child files and immediate child directories."""
    files = agg_all["files"]
    dirs = agg_all["dirs"]

    def rollup(path: str) -> dict:
        prefix = path + "/" if path else ""
        total = {k: 0 for k in LINE_KEYS}
        for p, f in files.items():
            if p.startswith(prefix) and "/" not in p[len(prefix):]:
                for k in LINE_KEYS:
                    total[k] += f[k]
        for d, metrics in dirs.items():
            if d and d != path and d.startswith(prefix) and "/" not in d[len(prefix):]:
                for k in LINE_KEYS:
                    total[k] += metrics[k]
        return total

    for path, d in dirs.items():
        assert metrics_of(d, LINE_KEYS) == rollup(path), f"rollup mismatch for {path!r}"


# ---------------------------------------------------------------------------
# File metrics
# ---------------------------------------------------------------------------
FILE_CASES = [
    ("a.txt", {"added": 5, "removed": 1, "growth": 4, "churn": 6, "mods": 2}),
    ("b.txt", {"added": 1, "removed": 0, "growth": 1, "churn": 1, "mods": 1}),
    ("bin.dat", {"added": 0, "removed": 0, "growth": 0, "churn": 0, "mods": 0}),
    (".mailmap", {"added": 1, "removed": 0, "growth": 1, "churn": 1, "mods": 1}),
    ("dir1/one.txt", {"added": 2, "removed": 0, "growth": 2, "churn": 2, "mods": 1}),
    ("dir2/one.txt", {"added": 2, "removed": 0, "growth": 2, "churn": 2, "mods": 1}),
    ("dir2/deep/two.txt", {"added": 1, "removed": 1, "growth": 0, "churn": 2, "mods": 2}),
    ("dir2/two.txt", {"added": 2, "removed": 0, "growth": 2, "churn": 2, "mods": 1}),
    ("y.txt", {"added": 1, "removed": 0, "growth": 1, "churn": 1, "mods": 1}),
    ("z.txt", {"added": 1, "removed": 0, "growth": 1, "churn": 1, "mods": 1}),
]


@pytest.mark.parametrize("path,expected", FILE_CASES)
def test_file_metrics(agg_all, path, expected):
    assert metrics_of(agg_all["files"][path]) == expected


def test_file_count_and_flags(agg_all):
    files = agg_all["files"]
    assert len(files) == 10
    assert set(files) == {p for p, _ in FILE_CASES}
    # Files present at HEAD.
    assert sorted(p for p, f in files.items() if f["in_head"]) == [
        ".mailmap",
        "b.txt",
        "dir2/one.txt",
        "dir2/two.txt",
        "y.txt",
        "z.txt",
    ]
    assert files["bin.dat"]["ever_binary"] is True
    assert sum(1 for f in files.values() if f["ever_binary"]) == 1
    assert all(f["is_file"] is True for f in files.values())


# ---------------------------------------------------------------------------
# Directory metrics
# ---------------------------------------------------------------------------
DIR_CASES = [
    ("dir1", {"added": 2, "removed": 0, "growth": 2, "churn": 2, "mods": 1}, 1),
    ("dir2", {"added": 5, "removed": 1, "growth": 4, "churn": 6, "mods": 3}, 3),
    ("dir2/deep", {"added": 1, "removed": 1, "growth": 0, "churn": 2, "mods": 2}, 1),
]


@pytest.mark.parametrize("path,expected,file_count", DIR_CASES)
def test_directory_metrics(agg_all, path, expected, file_count):
    d = agg_all["dirs"][path]
    assert metrics_of(d) == expected
    assert d["file_count"] == file_count
    assert d["is_file"] is False


def test_directory_set(agg_all):
    assert sorted(agg_all["dirs"]) == ["", "dir1", "dir2", "dir2/deep"]


# ---------------------------------------------------------------------------
# Authors (mailmap grouping + ownership)
# ---------------------------------------------------------------------------
def test_author_grouping_via_mailmap(agg_all):
    assert len(agg_all["authors"]) == 1
    a0 = agg_all["authors"][0]
    assert a0["label"] == "Proper Name"
    assert a0["commits"] == 13
    assert (a0["added"], a0["removed"], a0["churn"]) == (16, 2, 18)
    assert a0["mods"] == 7
    assert a0["ownership"] == 1.0
    assert a0["emails"] == ["dev1@example.com"]


def test_manual_merge_then_unmerge(ctx):
    conn, repo_id = ctx
    conn.execute(
        "INSERT INTO authors(repo_id,name,email,canonical_name,canonical_email,commit_count,churn)"
        " VALUES (?,?,?,?,?,0,0)",
        (repo_id, "Other Dev", "other@example.com", "Other Dev", "other@example.com"),
    )
    conn.commit()
    try:
        conn.execute(
            "INSERT INTO author_merge_ops(repo_id,label,members,created_ts) VALUES (?,?,?,?)",
            (
                repo_id,
                "Team Label",
                json.dumps(
                    [["Other Dev", "other@example.com"], ["Dev One", "dev1@example.com"]]
                ),
                1,
            ),
        )
        conn.commit()
        engine.invalidate(repo_id)

        merged = aggregate(conn, repo_id)
        assert len(merged["authors"]) == 1
        m0 = merged["authors"][0]
        assert m0["label"] == "Team Label"
        assert m0["commits"] == 13
        assert m0["churn"] == 18
        assert m0["ownership"] == 1.0
        assert m0["emails"] == ["dev1@example.com", "other@example.com"]
        assert len(m0["aliases"]) == 2
    finally:
        conn.execute("DELETE FROM author_merge_ops WHERE repo_id=?", (repo_id,))
        conn.execute("DELETE FROM authors WHERE repo_id=? AND email='other@example.com'", (repo_id,))
        conn.commit()
        engine.invalidate(repo_id)

    restored = aggregate(conn, repo_id)
    assert [a["label"] for a in restored["authors"]] == ["Proper Name"]


# ---------------------------------------------------------------------------
# Timeline / heatmap
# ---------------------------------------------------------------------------
def test_timeline_day_granularity(agg_all):
    timeline = agg_all["timeline"]
    assert timeline["granularity"] == "day"
    assert len(timeline["points"]) == 12  # the empty commit contributes nothing
    first = timeline["points"][0]
    assert (first["b"], first["added"], first["removed"], first["commits"]) == (
        "2024-01-01",
        6,
        0,
        1,
    )
    assert [p["b"] for p in timeline["points"]] == sorted(p["b"] for p in timeline["points"])


def test_author_timeline(agg_all):
    at = agg_all["author_timeline"]
    assert [a["label"] for a in at["authors"]] == ["Proper Name"]
    assert sum(sum(p["values"]) for p in at["points"]) == 18


def test_heatmap_only_covers_churned_files_and_buckets(agg_all):
    heat = agg_all["heatmap"]
    assert sorted(heat["files"]) == sorted(
        p for p, f in agg_all["files"].items() if f["churn"] > 0
    )
    assert len(heat["files"]) == 9
    assert len(heat["buckets"]) == 7  # rename / chmod-only days carry no churn
    assert all(v[2] > 0 for v in heat["values"])


# ---------------------------------------------------------------------------
# Scope resolution
# ---------------------------------------------------------------------------
def test_scope_directory(ctx):
    conn, repo_id = ctx
    agg = aggregate(conn, repo_id, path="dir2")
    assert agg["scope"] == {"path": "dir2", "kind": "dir"}
    assert metrics_of(agg["object"]) == {
        "added": 5,
        "removed": 1,
        "growth": 4,
        "churn": 6,
        "mods": 3,
    }
    assert sorted(agg["files"]) == ["dir2/deep/two.txt", "dir2/one.txt", "dir2/two.txt"]
    assert metrics_of(agg["dirs"]["dir2/deep"]) == {
        "added": 1,
        "removed": 1,
        "growth": 0,
        "churn": 2,
        "mods": 2,
    }
    # Repository totals stay global even when a scope is selected.
    assert agg["repo_totals"] == {"added": 16, "removed": 2, "growth": 14, "churn": 18}


def test_scope_nested_directory(ctx):
    conn, repo_id = ctx
    agg = aggregate(conn, repo_id, path="dir2/deep")
    assert agg["scope"]["kind"] == "dir"
    assert metrics_of(agg["object"]) == {
        "added": 1,
        "removed": 1,
        "growth": 0,
        "churn": 2,
        "mods": 2,
    }


def test_scope_file(ctx):
    conn, repo_id = ctx
    agg = aggregate(conn, repo_id, path="a.txt")
    assert agg["scope"] == {"path": "a.txt", "kind": "file"}
    assert metrics_of(agg["object"]) == {
        "added": 5,
        "removed": 1,
        "growth": 4,
        "churn": 6,
        "mods": 2,
    }
    assert agg["object"]["freq"] == round(2 / 13, 6)
    assert agg["object"]["is_file"] is True


def test_scope_binary_file(ctx):
    conn, repo_id = ctx
    agg = aggregate(conn, repo_id, path="bin.dat")
    assert metrics_of(agg["object"]) == {
        "added": 0,
        "removed": 0,
        "growth": 0,
        "churn": 0,
        "mods": 0,
    }
    assert agg["object"]["freq"] == 0.0 and agg["object"]["rate"] == 0.0


def test_scope_unknown_object_reports_error(ctx):
    conn, repo_id = ctx
    result = engine.compute(conn, fetch_repo(conn, repo_id), engine.FilterSpec(path="nope/missing.txt"))
    assert "error" in result


# ---------------------------------------------------------------------------
# Commit-set filters
# ---------------------------------------------------------------------------
def test_range_filter_lower_inclusive_upper_exclusive(ctx):
    conn, repo_id = ctx
    # c5 .. c8 (c9 starts exactly at the exclusive upper bound).
    agg = aggregate(conn, repo_id, mode="range", from_ts=1704448800, to_ts=1704794400)
    assert agg["n_commits"] == 4
    assert metrics_of(agg["object"]) == {
        "added": 2,
        "removed": 0,
        "growth": 2,
        "churn": 2,
        "mods": 2,
    }
    assert agg["object"]["freq"] == 0.5
    assert agg["object"]["rate"] == 0.5
    assert metrics_of(agg["files"]["b.txt"]) == {
        "added": 1,
        "removed": 0,
        "growth": 1,
        "churn": 1,
        "mods": 1,
    }


def test_range_filter_upper_bound_exclusive(ctx):
    conn, repo_id = ctx
    agg = aggregate(conn, repo_id, mode="range", to_ts=1704276000)  # before c3
    assert agg["n_commits"] == 2
    assert metrics_of(agg["object"]) == {
        "added": 8,
        "removed": 1,
        "growth": 7,
        "churn": 9,
        "mods": 2,
    }


def test_range_filter_from_only(ctx):
    conn, repo_id = ctx
    agg = aggregate(conn, repo_id, mode="range", from_ts=1704967200)  # c11 onward
    assert agg["n_commits"] == 3
    assert metrics_of(agg["object"]) == {
        "added": 4,
        "removed": 1,
        "growth": 3,
        "churn": 5,
        "mods": 2,
    }


def test_empty_commit_set_yields_zero_rates(ctx):
    conn, repo_id = ctx
    agg = aggregate(conn, repo_id, mode="range", from_ts=9999999999)
    assert agg["n_commits"] == 0
    assert agg["object"]["freq"] == 0.0
    assert agg["object"]["rate"] == 0.0
    assert agg["authors"] == []
    assert agg["heatmap"]["files"] == []


def _hash_for(conn, repo_id: int, subject: str) -> str:
    row = db.fetch_one(
        conn, "SELECT hash FROM commits WHERE repo_id=? AND subject=?", (repo_id, subject)
    )
    assert row is not None
    return row["hash"]


def test_manual_commit_list(ctx):
    conn, repo_id = ctx
    h_c2 = _hash_for(conn, repo_id, "c2")
    h_c13 = _hash_for(conn, repo_id, "c13-two-files")
    agg = aggregate(conn, repo_id, mode="list", hashes=(h_c2, h_c13))
    assert agg["n_commits"] == 2
    assert metrics_of(agg["object"]) == {
        "added": 4,
        "removed": 1,
        "growth": 3,
        "churn": 5,
        "mods": 2,
    }
    assert agg["files"]["a.txt"]["churn"] == 3
    assert agg["files"]["z.txt"]["churn"] == 1
    assert agg["files"]["b.txt"]["churn"] == 0

    # Unknown hashes are ignored rather than fatal.
    again = aggregate(conn, repo_id, mode="list", hashes=(h_c2, h_c13, "f" * 40))
    assert again["n_commits"] == 2


def test_author_filter(ctx):
    conn, repo_id = ctx
    author_id = db.fetch_one(
        conn, "SELECT id FROM authors WHERE repo_id=?", (repo_id,)
    )["id"]
    mine = aggregate(conn, repo_id, author_ids=(author_id,))
    assert metrics_of(mine["object"]) == {
        "added": 16,
        "removed": 2,
        "growth": 14,
        "churn": 18,
        "mods": 7,
    }
    nobody = aggregate(conn, repo_id, author_ids=(999999,))
    assert nobody["n_commits"] == 0
    assert metrics_of(nobody["object"]) == {
        "added": 0,
        "removed": 0,
        "growth": 0,
        "churn": 0,
        "mods": 0,
    }


# ---------------------------------------------------------------------------
# Table helpers + cache
# ---------------------------------------------------------------------------
def test_object_rows_sort_search_and_pagination(agg_all):
    rows = engine.object_rows(agg_all, "file", sort="churn")
    assert rows["total"] == 10
    assert rows["rows"][0]["path"] == "a.txt"

    searched = engine.object_rows(agg_all, "file", q="dir2/")
    assert sorted(r["path"] for r in searched["rows"]) == [
        "dir2/deep/two.txt",
        "dir2/one.txt",
        "dir2/two.txt",
    ]

    page = engine.object_rows(agg_all, "file", sort="path", order="asc", offset=2, limit=3)
    assert [r["path"] for r in page["rows"]] == ["b.txt", "bin.dat", "dir1/one.txt"]

    dirs = engine.object_rows(agg_all, "dir", sort="path", order="asc")
    assert [r["path"] for r in dirs["rows"]] == ["dir1", "dir2", "dir2/deep"]


def test_object_rows_scoped_directories(ctx):
    """Dir tables list directories strictly below the scope; the scope itself
    and the ''/parent-chain rollups never appear as rows."""
    conn, repo_id = ctx

    dir_agg = aggregate(conn, repo_id, path="dir2")
    assert sorted(dir_agg["dirs"]) == ["", "dir2", "dir2/deep"]
    scoped = engine.object_rows(dir_agg, "dir")
    assert [r["path"] for r in scoped["rows"]] == ["dir2/deep"]
    assert scoped["rows"][0]["churn"] == 2

    file_agg = aggregate(conn, repo_id, path="a.txt")
    assert engine.object_rows(file_agg, "dir") == {"total": 0, "rows": []}
    files = engine.object_rows(file_agg, "file")
    assert [r["path"] for r in files["rows"]] == ["a.txt"]


def test_page_top_authors(ctx, agg_all):
    conn, repo_id = ctx
    groups = engine.author_group_map(conn, fetch_repo(conn, repo_id))
    tops = engine.page_top_authors(conn, fetch_repo(conn, repo_id), engine.FilterSpec(), groups, ["a.txt", "y.txt"])
    assert tops["a.txt"][0]["churn"] == 6
    assert tops["a.txt"][0]["ownership"] == 1.0
    assert tops["y.txt"][0]["churn"] == 1

    dir_tops = engine.page_top_authors(
        conn, fetch_repo(conn, repo_id), engine.FilterSpec(), groups, dirs=["dir2"]
    )
    assert dir_tops["dir2"][0]["churn"] == 6


def test_cache_identity_and_data_version_bump(ctx):
    conn, repo_id = ctx
    spec = engine.FilterSpec(path="dir1")
    row = fetch_repo(conn, repo_id)
    first = engine.compute_cached(conn, row, spec)
    assert engine.compute_cached(conn, row, spec) is first
    assert engine.cache_info()["entries"] > 0

    conn.execute("UPDATE repos SET data_version = data_version + 1 WHERE id=?", (repo_id,))
    conn.commit()
    try:
        bumped = engine.compute_cached(conn, fetch_repo(conn, repo_id), spec)
        assert bumped is not first
        assert metrics_of(bumped["object"]) == metrics_of(first["object"])
    finally:
        conn.execute("UPDATE repos SET data_version = data_version - 1 WHERE id=?", (repo_id,))
        conn.commit()
        engine.invalidate(repo_id)
