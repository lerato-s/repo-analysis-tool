"""Deterministic git fixture repositories for the test suite.

``build_scratch_repo`` recreates the fixture whose git behaviour was verified
byte-by-byte during development (renames, cross-directory moves, binaries,
chmod-only commits, mailmap, empty commits). Its expected metrics are
hand-computed in ``test_metrics.py``.

``build_multi_author_repo`` has three identities (one without a mailmap)
used for manual-merge tests.
"""
from __future__ import annotations

import os
import subprocess
import zipfile
from io import BytesIO
from pathlib import Path

_GIT_ENV = {
    "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
    "HOME": os.environ.get("HOME", "/tmp"),
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
    "GIT_TERMINAL_PROMPT": "0",
    "LC_ALL": "C",
}


def _run(repo: Path, args: list[str], env_extra: dict | None = None) -> str:
    env = dict(_GIT_ENV)
    env.update(env_extra or {})
    proc = subprocess.run(
        ["git", "-C", str(repo), *args], env=env, capture_output=True, text=True
    )
    if proc.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed in {repo}:\n{proc.stderr}")
    return proc.stdout


def _init(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _run(repo, ["init", "-q", "-b", "main"])


def _commit(
    repo: Path,
    date: str,
    message: str,
    *,
    name: str = "Dev One",
    email: str = "dev1@example.com",
    allow_empty: bool = False,
) -> None:
    env = {
        "GIT_AUTHOR_DATE": date,
        "GIT_COMMITTER_DATE": date,
        "GIT_AUTHOR_NAME": name,
        "GIT_AUTHOR_EMAIL": email,
        "GIT_COMMITTER_NAME": name,
        "GIT_COMMITTER_EMAIL": email,
    }
    args = ["commit", "-q", "-m", message]
    if allow_empty:
        args.append("--allow-empty")
    _run(repo, args, env)


def _write(repo: Path, rel: str, text: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _add_all(repo: Path) -> None:
    _run(repo, ["add", "-A"])


# ---------------------------------------------------------------------------
# Scratch fixture (13 commits; the metric ground truth)
# ---------------------------------------------------------------------------
def build_scratch_repo(repo: Path) -> None:
    _init(repo)

    _write(repo, "a.txt", "l1\nl2\nl3\n")
    _write(repo, "dir1/one.txt", "t1\nt2\n")
    _write(repo, "dir2/deep/two.txt", "d1\n")
    _add_all(repo)
    _commit(repo, "2024-01-01T10:00:00+00:00", "c1")

    _write(repo, "a.txt", "l1\nl2x\nl3\nl4\n")
    _add_all(repo)
    _commit(repo, "2024-01-02T10:00:00+00:00", "c2")

    (repo / "bin.dat").write_bytes(b"\x00\x01\x02binarypayload")
    _add_all(repo)
    _commit(repo, "2024-01-03T10:00:00+00:00", "c3-binary-add")

    _run(repo, ["mv", "a.txt", "b.txt"])
    _commit(repo, "2024-01-04T10:00:00+00:00", "c4-pure-rename")

    _write(repo, "b.txt", "l1\nl2x\nl3\nl4\nl5\n")
    _add_all(repo)
    _commit(repo, "2024-01-05T10:00:00+00:00", "c5-append")

    os.chmod(repo / "b.txt", 0o755)
    _add_all(repo)
    _commit(repo, "2024-01-06T10:00:00+00:00", "c6-chmod-only")

    _run(repo, ["rm", "-q", "bin.dat"])
    _commit(repo, "2024-01-07T10:00:00+00:00", "c7-binary-delete")

    _write(repo, ".mailmap", "Proper Name <proper@example.com> Dev One <dev1@example.com>\n")
    _add_all(repo)
    _commit(repo, "2024-01-08T10:00:00+00:00", "c8-mailmap")

    _run(repo, ["mv", "dir1/one.txt", "dir2/one.txt"])
    _commit(repo, "2024-01-09T10:00:00+00:00", "c9-crossdir-move")

    _write(repo, "dir2/one.txt", "t1\nt2\nt3\nt4\n")
    _add_all(repo)
    _commit(repo, "2024-01-10T10:00:00+00:00", "c10-edit-moved")

    _commit(repo, "2024-01-11T10:00:00+00:00", "c11-empty", allow_empty=True)

    _run(repo, ["mv", "dir2/deep/two.txt", "dir2/two.txt"])
    _write(repo, "dir2/two.txt", "d1\nextra\n")
    _add_all(repo)
    _commit(repo, "2024-01-12T10:00:00+00:00", "c12-rename-plus-edit")

    _write(repo, "z.txt", "z\n")
    _write(repo, "y.txt", "y\n")
    _add_all(repo)
    _commit(repo, "2024-01-13T10:00:00+00:00", "c13-two-files")


# ---------------------------------------------------------------------------
# Multi-author fixture (3 identities, no mailmap) for merge tests
# ---------------------------------------------------------------------------
ALICE = ("Alice", "alice@example.com")
ALICE_ALT = ("Alice Alt", "alice.alt@example.com")
BOB = ("Bob", "bob@example.com")


def build_multi_author_repo(repo: Path) -> None:
    _init(repo)

    def who(ident: tuple[str, str]) -> dict:
        return {"name": ident[0], "email": ident[1]}

    _write(repo, "main.py", "m1\nm2\nm3\n")
    _add_all(repo)
    _commit(repo, "2024-02-01T10:00:00+00:00", "a1-main", **who(ALICE))

    _write(repo, "README.md", "# readme\nhello\n")
    _add_all(repo)
    _commit(repo, "2024-02-02T10:00:00+00:00", "b1-readme", **who(BOB))

    _write(repo, "src/util.py", "u1\nu2\n")
    _add_all(repo)
    _commit(repo, "2024-02-03T10:00:00+00:00", "a2-util", **who(ALICE))

    _write(repo, "src/util.py", "u1\nu2\nu3\nu4\n")
    _add_all(repo)
    _commit(repo, "2024-02-04T10:00:00+00:00", "b2-util", **who(BOB))

    _write(repo, "main.py", "m1\nm2x\nm3\n")
    _add_all(repo)
    _commit(repo, "2024-02-05T10:00:00+00:00", "a3-main", **who(ALICE))

    _write(repo, "main.py", "m1\nm2x\nm3\nm4\nm5\n")
    _add_all(repo)
    _commit(repo, "2024-02-06T10:00:00+00:00", "a4-main-alt", **who(ALICE_ALT))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def zip_dir(src: Path, arc_root: str, *, omit_git: bool = False) -> bytes:
    """Zip a repository directory (including .git) for upload tests."""
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(src.rglob("*")):
            rel = path.relative_to(src)
            if omit_git and rel.parts and rel.parts[0] == ".git":
                continue
            if path.is_dir():
                zf.writestr(f"{arc_root}/{rel.as_posix()}/", "")
            else:
                zf.write(path, arcname=f"{arc_root}/{rel.as_posix()}")
    return buf.getvalue()
