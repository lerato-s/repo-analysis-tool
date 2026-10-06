"""Repository ingestion sources: zip archives and remote clone URLs."""
from __future__ import annotations

import re
import shutil
import time
import uuid
import zipfile
from pathlib import Path, PurePosixPath

from .. import config
from . import gitcmd

_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
_URL_RE = re.compile(r"^(https?://|git://|ssh://|git@)[^\s]+$")


class SourceError(ValueError):
    """User-facing ingestion error."""


# ---------------------------------------------------------------------------
# Naming
# ---------------------------------------------------------------------------
def slugify(name: str, fallback: str = "repo") -> str:
    slug = _NAME_RE.sub("-", name.strip()).strip("-._")
    return slug[:80] or fallback


def name_from_url(url: str) -> str:
    part = url.rstrip("/").rsplit("/", 1)[-1]
    if part.endswith(".git"):
        part = part[:-4]
    return slugify(part)


def unique_name(conn, base: str) -> str:
    taken = {r["name"] for r in conn.execute("SELECT name FROM repos").fetchall()}
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


def validate_url(url: str) -> str:
    url = url.strip()
    if not _URL_RE.match(url):
        raise SourceError(
            "That does not look like a clone URL. Use https://, ssh:// or git@…"
        )
    return url


# ---------------------------------------------------------------------------
# Zip ingestion
# ---------------------------------------------------------------------------
def _safe_members(zf: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    members = []
    for member in zf.infolist():
        # Validate as a posix path with backslashes treated as separators so
        # "..\\evil" cannot escape on any platform.
        probe = member.filename.replace("\\", "/")
        parts = PurePosixPath(probe).parts
        if probe.startswith("/") or ".." in parts or re.match(r"^[A-Za-z]:", probe):
            raise SourceError(f"Archive contains an unsafe path: {member.filename!r}")
        members.append(member)
    return members


def extract_zip(zip_path: Path, progress=None) -> Path:
    """Safely extract a zip into a fresh temp dir; returns the extraction root."""
    root = config.TMP_DIR / f"unzip-{uuid.uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)
    notify = progress or (lambda frac, msg: None)
    with zipfile.ZipFile(zip_path) as zf:
        members = _safe_members(zf)
        total = max(len(members), 1)
        for i, member in enumerate(members):
            target = root / member.filename
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst, length=1 << 20)
            if i % 50 == 0 or i + 1 == total:
                notify((i + 1) / total, f"Extracting archive ({i + 1}/{total})")
    return root


def locate_repo_root(root: Path) -> tuple[Path, Path]:
    """Find the shallowest directory containing ``.git``.

    Returns ``(repo_dir, top_dir)`` where ``top_dir`` is what must be moved
    into the repositories directory (moving the whole extraction tree keeps
    relative ``gitdir:`` pointers inside ``.git`` files intact).
    """
    candidates: list[Path] = []
    for dirpath in sorted(root.rglob(".git")):
        rel = dirpath.relative_to(root)
        if any(part in (".git",) for part in rel.parts[:-1]):
            continue  # nested inside another .git — ignore
        candidates.append(dirpath.parent)
    if not candidates:
        raise SourceError(
            "No .git directory or file was found in the uploaded archive. "
            "Zip the repository including its .git folder."
        )
    candidates.sort(key=lambda p: (len(p.relative_to(root).parts), str(p)))
    return candidates[0], root


def validate_repo(repo_dir: Path) -> None:
    """Raise SourceError with a friendly message if git cannot use repo_dir."""
    gitdir = gitcmd.run_git(repo_dir, ["rev-parse", "--git-dir"], check=False)
    if gitdir.returncode != 0:
        raise SourceError(
            f"Not a usable git repository at {repo_dir.name!r}: "
            + gitdir.stderr.decode("utf-8", "replace").strip()
        )
    head = gitcmd.run_git(
        repo_dir, ["rev-parse", "--verify", "--quiet", "HEAD"], check=False
    )
    if head.returncode != 0:
        raise SourceError("This repository has no commits (HEAD is unborn).")
    real_gitdir = gitcmd.run_git(repo_dir, ["rev-parse", "--absolute-git-dir"])
    gd = Path(real_gitdir.stdout.decode("utf-8", "replace").strip())
    if (gd / "shallow").exists():
        raise SourceError(
            "This is a shallow clone. The tool needs the full history — "
            "use a full clone URL or a complete zip instead."
        )


def install_extracted(top_dir: Path, repo_root: Path, dest: Path) -> Path:
    """Move the extracted tree into place; returns the repo dir inside dest.

    The whole extraction tree is moved (not just the repo folder) so that
    relative ``gitdir:`` pointers inside a ``.git`` *file* stay valid.
    """
    rel = repo_root.relative_to(top_dir)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(top_dir), str(dest))
    return dest / rel if rel.parts else dest


def cleanup(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


# ---------------------------------------------------------------------------
# Remote clone
# ---------------------------------------------------------------------------
_RECEIVING = re.compile(r"Receiving objects:\s+(\d+)%")
_RESOLVING = re.compile(r"Resolving deltas:\s+(\d+)%")
_COMPRESSING = re.compile(r"Compressing objects:\s+(\d+)%")
_REMOTE = re.compile(r"Counting objects:\s+(\d+)%")


def clone_repository(url: str, dest: Path, progress=None) -> None:
    """Full ('deep') clone of a remote repository."""
    url = validate_url(url)
    notify = progress or (lambda frac, msg: None)
    dest.parent.mkdir(parents=True, exist_ok=True)
    argv = [
        "git",
        "-c", "credential.helper=",
        "-c", "advice.detachedHead=false",
        "clone",
        "--progress",
        "--",
        url,
        str(dest),
    ]

    state = {"frac": 0.0}

    def on_chunk(text: str) -> None:
        for piece in re.split(r"[\r\n]", text):
            piece = piece.strip()
            if not piece:
                continue
            m = _RECEIVING.search(piece)
            if m:
                frac = 0.05 + 0.80 * int(m.group(1)) / 100
            else:
                m = _RESOLVING.search(piece)
                if m:
                    frac = 0.85 + 0.12 * int(m.group(1)) / 100
                elif _COMPRESSING.search(piece) or _REMOTE.search(piece):
                    frac = 0.04
                elif piece.startswith("Cloning into"):
                    frac = 0.02
                else:
                    continue
            state["frac"] = max(state["frac"], frac)
            notify(state["frac"], piece)

    notify(0.0, f"Cloning {url}")
    try:
        gitcmd.run_streaming_stderr(
            dest.parent, argv, chunk_handler=on_chunk, timeout=config.CLONE_TIMEOUT_S
        )
    except gitcmd.GitError as exc:
        # Surface the most useful line of git's error output.
        lines = [ln.strip() for ln in exc.stderr.splitlines() if ln.strip()]
        detail = lines[-1] if lines else str(exc)
        if "Authentication failed" in exc.stderr or "could not read Username" in exc.stderr:
            raise SourceError(
                "Authentication is required for that URL. Only public "
                "repositories can be cloned."
            ) from exc
        raise SourceError(detail) from exc
    notify(1.0, "Clone complete")


def free_space_ok(required_bytes: int = 0) -> bool:
    usage = shutil.disk_usage(config.DATA_DIR if config.DATA_DIR.exists() else config.BASE_DIR)
    return usage.free - required_bytes > 512 * 1024 * 1024


def timestamp() -> int:
    return int(time.time())
