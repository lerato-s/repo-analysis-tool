"""Thin, streaming-safe wrappers around the ``git`` CLI.

All invocations run non-interactively (no credential prompts, no pager) and
with ``LC_ALL=C`` so error messages and progress output are stable enough to
parse. Nothing is executed through a shell.
"""
from __future__ import annotations

import os
import subprocess
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Iterator

_GIT_ENV = {
    **os.environ,
    "GIT_TERMINAL_PROMPT": "0",  # never block waiting for credentials
    "GIT_ASKPASS": "true",
    "GIT_PAGER": "cat",
    "LC_ALL": "C",
}


class GitError(RuntimeError):
    """A git invocation failed; carries git's stderr tail for the UI."""

    def __init__(self, argv: list[str], returncode: int, stderr: str):
        self.argv = argv
        self.returncode = returncode
        self.stderr = stderr
        detail = stderr.strip() or f"exit code {returncode}"
        super().__init__(f"git {argv[1:]} failed: {detail}")


def _base_args(repo_dir: Path) -> list[str]:
    return [
        "git",
        "-C", str(repo_dir),
        "-c", "log.showSignature=false",
        "-c", "core.quotepath=false",
        "-c", "color.ui=never",
    ]


def run_git(
    repo_dir: Path,
    args: list[str],
    *,
    check: bool = True,
    timeout: float | None = None,
    input_bytes: bytes | None = None,
) -> subprocess.CompletedProcess:
    """Run a git command to completion, capturing stdout/stderr."""
    argv = _base_args(repo_dir) + list(args)
    proc = subprocess.run(
        argv, capture_output=True, timeout=timeout, input=input_bytes, env=_GIT_ENV
    )
    if check and proc.returncode != 0:
        raise GitError(argv, proc.returncode, proc.stderr.decode("utf-8", "replace"))
    return proc


class StreamedGit:
    """A running git process whose stdout is being consumed by the caller."""

    def __init__(self, proc: subprocess.Popen, tail: bytearray, argv: list[str]):
        self.proc = proc
        self._tail = tail
        self.argv = argv

    @property
    def stdout(self):
        return self.proc.stdout

    @property
    def stderr_tail(self) -> str:
        return bytes(self._tail).decode("utf-8", "replace")

    def wait(self, timeout: float | None = None) -> None:
        """Wait for the process; raise GitError on non-zero exit or timeout."""
        try:
            rc = self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
            raise GitError(self.argv, -1, f"timed out. {self.stderr_tail}") from None
        if rc != 0:
            raise GitError(self.argv, rc, self.stderr_tail)


@contextmanager
def stream_git(
    repo_dir: Path,
    args: list[str],
    *,
    timeout: float | None = None,
    stderr_handler: Callable[[bytes], None] | None = None,
) -> Iterator[StreamedGit]:
    """Run git with a bounded stderr drain thread (deadlock-free streaming)."""
    argv = _base_args(repo_dir) + list(args)
    proc = subprocess.Popen(
        argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=_GIT_ENV
    )
    tail = bytearray()

    def drain() -> None:
        assert proc.stderr is not None
        while True:
            chunk = proc.stderr.read(65536)
            if not chunk:
                break
            tail.extend(chunk)
            if len(tail) > 16384:
                del tail[:-16384]
            if stderr_handler is not None:
                stderr_handler(chunk)

    drainer = threading.Thread(target=drain, daemon=True)
    drainer.start()
    watchdog: threading.Timer | None = None
    if timeout:
        watchdog = threading.Timer(timeout, proc.kill)
        watchdog.daemon = True
        watchdog.start()
    try:
        yield StreamedGit(proc, tail, argv)
    finally:
        if watchdog is not None:
            watchdog.cancel()
        drainer.join(timeout=5)


def run_streaming_stderr(
    cwd: Path,
    argv: list[str],
    *,
    chunk_handler: Callable[[str], None] | None = None,
    timeout: float | None = None,
) -> None:
    """Run a command (e.g. ``git clone --progress``) parsing stderr chunks.

    Progress output on stderr uses carriage returns, so chunks - not lines -
    are handed to the callback.
    """
    proc = subprocess.Popen(
        argv,
        cwd=str(cwd),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        env=_GIT_ENV,
    )
    tail = bytearray()
    assert proc.stderr is not None
    while True:
        chunk = proc.stderr.read(4096)
        if not chunk:
            break
        tail.extend(chunk)
        if len(tail) > 16384:
            del tail[:-16384]
        if chunk_handler is not None:
            chunk_handler(chunk.decode("utf-8", "replace"))
    try:
        rc = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        raise GitError(argv, -1, f"timed out after {timeout}s") from None
    if rc != 0:
        raise GitError(argv, rc, bytes(tail).decode("utf-8", "replace"))
