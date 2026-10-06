"""End-to-end API tests: multipart upload / clone -> worker -> analyzer ->
engine -> JSON endpoints, all against the sandbox data dir created in
``conftest`` (``RAT_DATA_DIR``), so real data can never be touched.

Reuses the same hand-computed ground truth as ``test_metrics.py``.
"""
from __future__ import annotations

import shutil
import time

import pytest

from app import config, db
from tests import fixtures

SCRATCH_TOTALS = {"added": 16, "removed": 2, "growth": 14, "churn": 18}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def wait_settled(client, repo_id: int, timeout: float = 300.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        row = client.get(f"/api/repos/{repo_id}").json()
        if row["status"] in ("ready", "error"):
            return row
        time.sleep(0.15)
    raise AssertionError(f"ingestion of repo {repo_id} did not settle in {timeout}s")


def wait_version(client, repo_id: int, version: int, timeout: float = 300.0) -> dict:
    """Wait until a *new* analysis (higher data_version) is ready."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        row = client.get(f"/api/repos/{repo_id}").json()
        if row["status"] == "error":
            return row
        if row["status"] == "ready" and row["data_version"] > version:
            return row
        time.sleep(0.15)
    raise AssertionError(f"re-analysis of repo {repo_id} did not finish in {timeout}s")


def upload(client, repo, name: str, *, arc_root: str = "fixture", omit_git: bool = False) -> dict:
    payload = fixtures.zip_dir(repo, arc_root, omit_git=omit_git)
    r = client.post(
        "/api/repos/upload",
        files={"file": (f"{name}.zip", payload, "application/zip")},
        data={"name": name},
    )
    assert r.status_code == 201, r.text
    return wait_settled(client, r.json()["id"])


def brief(m: dict) -> dict:
    return {k: m[k] for k in ("added", "removed", "growth", "churn", "mods")}


# ---------------------------------------------------------------------------
# Module fixtures (torn down with a repo delete)
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def scratch_api(app_client, scratch_repo):
    row = upload(app_client, scratch_repo, "api-scratch")
    assert row["status"] == "ready", row.get("error")
    yield row
    app_client.delete(f"/api/repos/{row['id']}")


@pytest.fixture(scope="module")
def multi_api(app_client, multi_repo):
    # Nested wrapper dir exercises repo-root discovery inside the archive.
    row = upload(app_client, multi_repo, "api-multi", arc_root="wrapper/fixture")
    assert row["status"] == "ready", row.get("error")
    yield row
    app_client.delete(f"/api/repos/{row['id']}")


# ---------------------------------------------------------------------------
# Repositories
# ---------------------------------------------------------------------------
def test_health_and_listing(app_client, scratch_api):
    assert app_client.get("/api/health").json()["ok"] is True
    listing = app_client.get("/api/repos").json()["repos"]
    assert any(r["id"] == scratch_api["id"] for r in listing)


def test_spa_deep_links_are_served(app_client):
    """Reloading a client-side route (e.g. a dashboard deep link) must serve
    the SPA shell, not a 404."""
    if not config.FRONTEND_DIST.is_dir():
        pytest.skip("frontend bundle has not been built")
    for path in ("/", "/r/123", "/nested/route"):
        res = app_client.get(path)
        assert res.status_code == 200, path
        assert 'id="root"' in res.text
        assert res.headers["content-type"].startswith("text/html")
    # Missing API paths must stay JSON 404s — never the SPA shell.
    res = app_client.get("/api/does-not-exist")
    assert res.status_code == 404
    assert res.json()["detail"] == "Not found."


def test_repo_detail_matches_fixture(app_client, scratch_api):
    detail = app_client.get(f"/api/repos/{scratch_api['id']}").json()
    assert detail["status"] == "ready"
    assert detail["error"] is None
    assert detail["commit_count"] == 13
    assert detail["change_count"] == 16
    assert detail["binary_count"] == 2
    assert detail["obj_count"] == 10
    assert detail["mailmap_kind"] == "worktree"
    assert detail["branch"] == "main"
    assert detail["kind"] == "zip"


def test_unknown_repo_is_404(app_client):
    assert app_client.get("/api/repos/999999").status_code == 404
    assert app_client.delete("/api/repos/999999").status_code == 404
    assert app_client.get("/api/repos/999999/commits").status_code == 404


def test_upload_and_clone_validation(app_client):
    r = app_client.post(
        "/api/repos/upload", files={"file": ("x.txt", b"not a zip", "text/plain")}
    )
    assert r.status_code == 400
    r = app_client.post(
        "/api/repos/upload", files={"file": ("x.zip", b"", "application/zip")}
    )
    assert r.status_code == 400
    r = app_client.post("/api/repos/clone", json={"url": "definitely not a url"})
    assert r.status_code == 400


def test_zip_without_git_reports_friendly_error(app_client, scratch_repo):
    row = upload(app_client, scratch_repo, "api-nogit", arc_root="plain", omit_git=True)
    assert row["status"] == "error"
    assert "No .git directory" in (row["error"] or "")
    assert app_client.delete(f"/api/repos/{row['id']}").status_code == 200


def test_clone_url_ingestion(app_client, scratch_repo, monkeypatch):
    """The clone path is exercised offline by stubbing only the network step."""
    from app.ingest import sources

    calls: dict = {}

    def fake_clone(url, dest, progress=None):
        calls["url"] = url
        shutil.copytree(scratch_repo, dest, symlinks=True)
        if progress is not None:
            progress(1.0, "clone complete")

    monkeypatch.setattr(sources, "clone_repository", fake_clone)
    url = "https://example.com/team/fixture.git"
    r = app_client.post("/api/repos/clone", json={"url": url, "name": "api-clone"})
    assert r.status_code == 201, r.text
    row = wait_settled(app_client, r.json()["id"])
    assert row["status"] == "ready", row.get("error")
    assert row["kind"] == "clone"
    assert row["origin"] == url
    assert row["origin_label"] == url
    assert calls["url"] == url
    assert row["commit_count"] == 13

    assert app_client.delete(f"/api/repos/{row['id']}").status_code == 200
    assert not (config.REPOS_DIR / "api-clone").exists()


def test_multi_repo_support(app_client, scratch_api, multi_api):
    ids = {r["id"] for r in app_client.get("/api/repos").json()["repos"]}
    assert {scratch_api["id"], multi_api["id"]} <= ids


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------
def test_analytics_root(app_client, scratch_api):
    rid = scratch_api["id"]
    a = app_client.post(f"/api/repos/{rid}/analytics", json={}).json()
    k = a["kpis"]
    assert k["n_commits"] == 13
    assert brief(k["object"]) == {
        "added": 16,
        "removed": 2,
        "growth": 14,
        "churn": 18,
        "mods": 7,
    }
    assert (k["object"]["freq"], k["object"]["rate"]) == (round(7 / 13, 6), round(18 / 13, 6))
    assert k["repo_totals"] == SCRATCH_TOTALS
    assert (k["file_count"], k["dir_count"], k["author_count"]) == (10, 4, 1)
    assert a["repo"]["id"] == rid
    assert a["filter"]["path"] == ""
    assert a["authors"][0]["label"] == "Proper Name"
    assert a["authors"][0]["ownership"] == 1.0
    assert a["timeline"]["granularity"] == "day"
    assert len(a["timeline"]["points"]) == 12
    assert [f["path"] for f in a["top_files"][:2]] == ["a.txt", "dir1/one.txt"]
    assert (len(a["heatmap"]["buckets"]), len(a["heatmap"]["files"])) == (7, 9)
    assert a["dirs"][0]["path"] == ""  # root sorts first (highest churn)
    assert a["truncated"] is False


def test_analytics_scoped_and_filtered(app_client, scratch_api):
    rid = scratch_api["id"]

    scoped = app_client.post(f"/api/repos/{rid}/analytics", json={"path": "dir2"}).json()
    assert brief(scoped["kpis"]["object"]) == {
        "added": 5,
        "removed": 1,
        "growth": 4,
        "churn": 6,
        "mods": 3,
    }
    assert scoped["kpis"]["file_count"] == 3
    assert sorted(d["path"] for d in scoped["dirs"]) == ["", "dir2", "dir2/deep"]
    assert all(f["path"].startswith("dir2/") for f in scoped["top_files"])
    # Repository totals stay global while an object scope is selected.
    assert scoped["kpis"]["repo_totals"]["churn"] == 18

    ranged = app_client.post(
        f"/api/repos/{rid}/analytics",
        json={"mode": "range", "from_ts": 1704448800, "to_ts": 1704794400},
    ).json()
    assert ranged["kpis"]["n_commits"] == 4
    assert brief(ranged["kpis"]["object"]) == {
        "added": 2,
        "removed": 0,
        "growth": 2,
        "churn": 2,
        "mods": 2,
    }

    empty = app_client.post(
        f"/api/repos/{rid}/analytics", json={"mode": "range", "from_ts": 9999999999}
    ).json()
    assert empty["kpis"]["n_commits"] == 0
    assert (empty["kpis"]["object"]["freq"], empty["kpis"]["object"]["rate"]) == (0.0, 0.0)
    assert empty["authors"] == []


def test_analytics_manual_commit_list(app_client, scratch_api):
    rid = scratch_api["id"]
    h2 = app_client.get(f"/api/repos/{rid}/commits", params={"q": "c2"}).json()["rows"][0]["hash"]
    h13 = app_client.get(
        f"/api/repos/{rid}/commits", params={"q": "c13-two-files"}
    ).json()["rows"][0]["hash"]

    a = app_client.post(
        f"/api/repos/{rid}/analytics", json={"mode": "list", "hashes": [h2, h13]}
    ).json()
    assert a["kpis"]["n_commits"] == 2
    assert brief(a["kpis"]["object"]) == {
        "added": 4,
        "removed": 1,
        "growth": 3,
        "churn": 5,
        "mods": 2,
    }
    assert a["filter"]["hashes_count"] == 2


def test_analytics_author_filter(app_client, scratch_api):
    rid = scratch_api["id"]
    base = app_client.post(f"/api/repos/{rid}/analytics", json={}).json()
    key = base["authors"][0]["key"]

    mine = app_client.post(
        f"/api/repos/{rid}/analytics", json={"author_keys": [key]}
    ).json()
    assert mine["kpis"]["object"]["churn"] == 18

    stale = app_client.post(
        f"/api/repos/{rid}/analytics", json={"author_keys": [999999]}
    ).json()
    assert stale["kpis"]["n_commits"] == 0


def test_analytics_file_scope_and_unknown_object(app_client, scratch_api):
    rid = scratch_api["id"]
    a = app_client.post(f"/api/repos/{rid}/analytics", json={"path": "a.txt"}).json()
    assert brief(a["kpis"]["object"]) == {
        "added": 5,
        "removed": 1,
        "growth": 4,
        "churn": 6,
        "mods": 2,
    }
    assert a["kpis"]["object"]["is_file"] is True
    assert app_client.post(f"/api/repos/{rid}/analytics", json={"path": "nope/missing"}).status_code == 400


# ---------------------------------------------------------------------------
# Object tables
# ---------------------------------------------------------------------------
def test_object_tables(app_client, scratch_api):
    rid = scratch_api["id"]

    files = app_client.post(
        f"/api/repos/{rid}/objects", json={}, params={"kind": "file"}
    ).json()
    assert files["kind"] == "file"
    assert files["total"] == 10
    assert files["rows"][0]["path"] == "a.txt"
    assert files["rows"][0]["tops"][0]["ownership"] == 1.0

    dirs = app_client.post(
        f"/api/repos/{rid}/objects",
        json={},
        params={"kind": "dir", "sort": "path", "order": "asc"},
    ).json()
    assert [r["path"] for r in dirs["rows"]] == ["dir1", "dir2", "dir2/deep"]
    assert [r["file_count"] for r in dirs["rows"]] == [1, 3, 1]
    assert dirs["rows"][1]["tops"][0]["churn"] == 6

    searched = app_client.post(
        f"/api/repos/{rid}/objects", json={}, params={"kind": "file", "q": "dir2/"}
    ).json()
    assert searched["total"] == 3

    page = app_client.post(
        f"/api/repos/{rid}/objects",
        json={},
        params={"kind": "file", "sort": "path", "order": "asc", "limit": 3, "offset": 2},
    ).json()
    assert [r["path"] for r in page["rows"]] == ["b.txt", "bin.dat", "dir1/one.txt"]

    # Scoped dir table: only directories strictly below the scope — the scope
    # itself and its ''/parent rollup entries must never become rows.
    scoped_dirs = app_client.post(
        f"/api/repos/{rid}/objects",
        json={"path": "dir2"},
        params={"kind": "dir", "sort": "path", "order": "asc"},
    ).json()
    assert [r["path"] for r in scoped_dirs["rows"]] == ["dir2/deep"]
    assert scoped_dirs["total"] == 1
    assert scoped_dirs["rows"][0]["tops"][0]["churn"] == 2

    file_scope_dirs = app_client.post(
        f"/api/repos/{rid}/objects", json={"path": "a.txt"}, params={"kind": "dir"}
    ).json()
    assert (file_scope_dirs["total"], file_scope_dirs["rows"]) == (0, [])


# ---------------------------------------------------------------------------
# Commit explorer
# ---------------------------------------------------------------------------
def test_commit_explorer(app_client, scratch_api):
    rid = scratch_api["id"]

    listing = app_client.get(f"/api/repos/{rid}/commits").json()
    assert listing["total"] == 13
    assert listing["rows"][0]["subject"] == "c13-two-files"  # newest first
    assert listing["rows"][0]["author_label"] == "Proper Name"

    assert app_client.get(f"/api/repos/{rid}/commits", params={"q": "c2"}).json()["total"] == 1

    h2 = app_client.get(f"/api/repos/{rid}/commits", params={"q": "c2"}).json()["rows"][0]["hash"]
    assert app_client.get(f"/api/repos/{rid}/commits", params={"q": h2[:8]}).json()["total"] == 1

    unknown = app_client.get(f"/api/repos/{rid}/commits", params={"q": "zzz-nothing"}).json()
    assert (unknown["total"], unknown["rows"]) == (0, [])

    hashes = app_client.get(f"/api/repos/{rid}/commits/hashes").json()
    assert len(hashes["hashes"]) == 13
    assert hashes["truncated"] is False

    detail = app_client.get(f"/api/repos/{rid}/commits/{h2[:8]}").json()
    assert detail["commit"]["hash"] == h2
    assert (detail["commit"]["added"], detail["commit"]["removed"]) == (2, 1)
    assert [(f["path"], f["added"], f["removed"]) for f in detail["files"]] == [("a.txt", 2, 1)]

    h13 = app_client.get(
        f"/api/repos/{rid}/commits", params={"q": "c13-two-files"}
    ).json()["rows"][0]["hash"]
    detail13 = app_client.get(f"/api/repos/{rid}/commits/{h13}").json()
    assert sorted(f["path"] for f in detail13["files"]) == ["y.txt", "z.txt"]

    assert app_client.get(f"/api/repos/{rid}/commits/deadbeef").status_code == 404


# ---------------------------------------------------------------------------
# Authors + manual merge
# ---------------------------------------------------------------------------
def test_authors_payload(app_client, scratch_api):
    rid = scratch_api["id"]
    au = app_client.get(f"/api/repos/{rid}/authors").json()
    assert [g["label"] for g in au["groups"]] == ["Proper Name"]
    assert au["ops"] == []
    assert au["mailmap_kind"] == "worktree"
    assert au["identity_count"] == 1


def test_manual_merge_flow(app_client, scratch_api):
    rid = scratch_api["id"]
    conn = db.connect()
    # A second identity has to exist before it can be merged (mirrors what a
    # user sees in the dashboard after a multi-author import).
    conn.execute(
        "INSERT INTO authors(repo_id,name,email,canonical_name,canonical_email,commit_count,churn)"
        " VALUES (?,?,?,?,?,0,0)",
        (rid, "Other Dev", "other@example.com", "Other Dev", "other@example.com"),
    )
    conn.commit()
    try:
        one_member = app_client.post(
            f"/api/repos/{rid}/authors/merge",
            json={"label": "X", "members": [["Dev One", "dev1@example.com"]]},
        )
        assert one_member.status_code == 422

        unknown_ids = app_client.post(
            f"/api/repos/{rid}/authors/merge",
            json={"label": "X", "members": [["nope", "a@b.c"], ["nope2", "d@e.f"]]},
        )
        assert unknown_ids.status_code == 400

        merged = app_client.post(
            f"/api/repos/{rid}/authors/merge",
            json={
                "label": "Team X",
                "members": [["Dev One", "dev1@example.com"], ["Other Dev", "other@example.com"]],
            },
        )
        assert merged.status_code == 200, merged.text
        payload = merged.json()
        assert [g["label"] for g in payload["groups"]] == ["Team X"]
        assert len(payload["ops"]) == 1
        assert sorted(payload["ops"][0]["members"]) == [
            ["Dev One", "dev1@example.com"],
            ["Other Dev", "other@example.com"],
        ]

        after = app_client.post(f"/api/repos/{rid}/analytics", json={}).json()
        assert [a["label"] for a in after["authors"]] == ["Team X"]
        assert after["authors"][0]["commits"] == 13

        filtered = app_client.post(
            f"/api/repos/{rid}/analytics", json={"author_keys": [payload["groups"][0]["key"]]}
        ).json()
        assert filtered["kpis"]["object"]["churn"] == 18

        unmerged = app_client.delete(
            f"/api/repos/{rid}/authors/ops/{payload['ops'][0]['id']}"
        ).json()
        assert sorted(g["label"] for g in unmerged["groups"]) == ["Other Dev", "Proper Name"]

        restored = app_client.post(f"/api/repos/{rid}/analytics", json={}).json()
        assert [a["label"] for a in restored["authors"]] == ["Proper Name"]

        assert app_client.delete(f"/api/repos/{rid}/authors/ops/999999").status_code == 404
    finally:
        conn.execute("DELETE FROM authors WHERE repo_id=? AND email='other@example.com'", (rid,))
        conn.commit()


def test_multi_author_repo_and_merge(app_client, multi_api):
    rid = multi_api["id"]
    assert multi_api["commit_count"] == 6
    assert multi_api["mailmap_kind"] == "none"

    au = app_client.get(f"/api/repos/{rid}/authors").json()
    assert au["identity_count"] == 3
    assert [g["label"] for g in au["groups"]] == ["Alice", "Bob", "Alice Alt"]
    assert [g["commits"] for g in au["groups"]] == [3, 2, 1]
    assert [g["churn"] for g in au["groups"]] == [7, 4, 2]
    assert [g["ownership"] for g in au["groups"]] == [
        round(7 / 13, 6),
        round(4 / 13, 6),
        round(2 / 13, 6),
    ]

    base = app_client.post(f"/api/repos/{rid}/analytics", json={}).json()
    assert base["kpis"]["n_commits"] == 6
    assert base["kpis"]["repo_totals"] == {"added": 12, "removed": 1, "growth": 11, "churn": 13}
    assert brief(base["kpis"]["object"]) == {
        "added": 12,
        "removed": 1,
        "growth": 11,
        "churn": 13,
        "mods": 6,
    }
    files = {f["path"]: f for f in base["top_files"]}
    assert files["main.py"]["churn"] == 7
    assert files["main.py"]["mods"] == 3
    assert files["src/util.py"]["churn"] == 4

    src = next(d for d in base["dirs"] if d["path"] == "src")
    assert (src["churn"], src["file_count"]) == (4, 1)

    merged = app_client.post(
        f"/api/repos/{rid}/authors/merge",
        json={
            "label": "Alice M",
            "members": [["Alice", "alice@example.com"], ["Alice Alt", "alice.alt@example.com"]],
        },
    )
    assert merged.status_code == 200, merged.text
    payload = merged.json()
    assert [g["label"] for g in payload["groups"]] == ["Alice M", "Bob"]
    assert payload["groups"][0]["commits"] == 4
    assert payload["groups"][0]["ownership"] == round(9 / 13, 6)

    key = payload["groups"][0]["key"]
    filtered = app_client.post(
        f"/api/repos/{rid}/analytics", json={"author_keys": [key]}
    ).json()
    assert filtered["kpis"]["n_commits"] == 4
    assert filtered["kpis"]["object"]["churn"] == 9
    mine = {f["path"]: f for f in filtered["top_files"]}
    assert mine["main.py"]["churn"] == 7
    assert mine["src/util.py"]["churn"] == 2

    commits = app_client.get(
        f"/api/repos/{rid}/commits", params={"author_keys": str(key)}
    ).json()
    assert commits["total"] == 4

    unmerged = app_client.delete(
        f"/api/repos/{rid}/authors/ops/{payload['ops'][0]['id']}"
    ).json()
    assert sorted(g["label"] for g in unmerged["groups"]) == ["Alice", "Alice Alt", "Bob"]


# ---------------------------------------------------------------------------
# Re-analysis + delete
# ---------------------------------------------------------------------------
def test_reanalyze_and_delete(app_client, scratch_repo):
    row = upload(app_client, scratch_repo, "api-del", arc_root="wrapper/del")
    rid = row["id"]
    checkout = config.REPOS_DIR / "api-del"
    zip_path = config.UPLOADS_DIR / "api-del.zip"
    assert (checkout / "wrapper" / "del" / ".git").exists()
    assert zip_path.is_file()

    before = row
    r = app_client.post(f"/api/repos/{rid}/reanalyze")
    assert r.status_code == 202
    # While the worker holds the repo, conflicting actions are rejected.
    assert app_client.post(f"/api/repos/{rid}/reanalyze").status_code == 409
    assert app_client.delete(f"/api/repos/{rid}").status_code == 409

    after = wait_version(app_client, rid, before["data_version"])
    assert after["status"] == "ready"
    assert after["commit_count"] == 13

    # A *full* re-analysis re-fetches the source archive.
    r = app_client.post(f"/api/repos/{rid}/reanalyze", params={"full": "true"})
    assert r.status_code == 202
    after2 = wait_version(app_client, rid, after["data_version"])
    assert after2["status"] == "ready"
    assert after2["commit_count"] == 13
    # Re-installing must not nest the extraction tree one level deeper.
    assert (checkout / "wrapper" / "del" / ".git").exists()
    assert not (checkout / "wrapper" / "del" / "wrapper").exists()

    assert app_client.delete(f"/api/repos/{rid}").status_code == 200
    assert not checkout.exists()
    assert not zip_path.exists()
    assert app_client.get(f"/api/repos/{rid}").status_code == 404
