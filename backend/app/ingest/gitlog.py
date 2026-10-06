"""Streaming parser for ``git log --numstat -z`` output.

The exact byte layout was verified empirically (see the project README's
"Verified git behaviour" notes) for these cases: root commit, edits, real
binary add/delete, pure renames, cross-directory moves, chmod-only changes,
empty commits, multi-file commits and rename+edit combinations.

Layout
------
* One record per commit starts with ``\\x1e`` followed by ``\\x1f``-separated
  header fields (hash, raw name/email, mailmap-canonical name/email, committer
  timestamp, parent hashes, subject) and is terminated by ``\\x00``.
* Only the *first* stats record after a header may be prefixed by one ``\\n``.
* Stats records: ``<added>\\t<removed>\\t<path>`` terminated by ``\\x00``.
  ``-`` for either count marks a binary file (stored with 0/0 and flagged).
* Rename/copy records end right after the second tab; the *two following*
  NUL-terminated tokens are the old then the new path. Metrics are attributed
  to the new path (the spec requires renames at 50% similarity not to change
  metrics).
* Empty commits emit a header with no stats records.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import BinaryIO, Iterator

RS = b"\x1e"  # record separator preceding each commit header
US = b"\x1f"  # header field separator


@dataclass(slots=True)
class CommitHeader:
    hash: str
    author_name: str
    author_email: str
    canonical_name: str
    canonical_email: str
    ts: int
    parent: str | None
    subject: str


@dataclass(slots=True)
class FileChange:
    path: str
    old_path: str | None
    added: int
    removed: int
    binary: bool


def _decode(raw: bytes) -> str:
    # Paths/names may legally contain non-UTF-8 bytes; degrade gracefully.
    return raw.decode("utf-8", "replace")


def _parse_header(raw: bytes) -> CommitHeader:
    # maxsplit keeps any stray separators inside the subject intact.
    fields = raw.split(US, 7)
    if len(fields) != 8:
        raise ValueError(f"malformed commit header: {fields[:2]!r}...")
    hash_, an, ae, can, cae, ct, parents, subject = fields
    parent = _decode(parents).split(" ", 1)[0] or None
    return CommitHeader(
        hash=_decode(hash_),
        author_name=_decode(an),
        author_email=_decode(ae),
        canonical_name=_decode(can) or _decode(an),
        canonical_email=_decode(cae) or _decode(ae),
        ts=int(ct),
        parent=parent,
        subject=_decode(subject),
    )


def iter_tokens(stream: BinaryIO, chunk_size: int = 1 << 20) -> Iterator[bytes]:
    """Yield NUL-separated tokens from a byte stream without buffering it all."""
    buf = b""
    while True:
        chunk = stream.read(chunk_size)
        if not chunk:
            break
        buf += chunk
        parts = buf.split(b"\x00")
        buf = parts.pop()
        yield from parts
    if buf:
        yield buf


def parse_log_stream(
    stream: BinaryIO,
) -> Iterator[tuple[CommitHeader, list[FileChange]]]:
    """Yield ``(header, changes)`` for every commit in the stream."""
    header: CommitHeader | None = None
    changes: list[FileChange] = []
    rename_wait = 0          # 2 = expect old path, 1 = expect new path
    rename_old: bytes = b""
    pending: tuple[int, int, bool] | None = None  # counts of the pending rename

    for raw in iter_tokens(stream):
        if raw[:1] == RS:
            if header is not None:
                yield header, changes
            header = _parse_header(raw[1:])
            changes = []
            rename_wait = 0
            pending = None
            continue
        if header is None:
            continue
        tok = raw[1:] if raw[:1] == b"\n" else raw
        if not tok:
            continue

        if rename_wait == 2:
            rename_old = tok
            rename_wait = 1
            continue
        if rename_wait == 1:
            assert pending is not None
            added, removed, binary = pending
            changes.append(
                FileChange(
                    path=_decode(tok),
                    old_path=_decode(rename_old),
                    added=added,
                    removed=removed,
                    binary=binary,
                )
            )
            rename_wait = 0
            pending = None
            continue

        fields = tok.split(b"\t", 2)
        if len(fields) != 3:
            continue  # defensive: unknown record shape, skip
        a_raw, r_raw, path_raw = fields
        binary = a_raw == b"-"
        added = 0 if binary else int(a_raw)
        removed = 0 if binary else int(r_raw)
        if path_raw == b"":
            pending = (added, removed, binary)
            rename_wait = 2
        else:
            changes.append(
                FileChange(
                    path=_decode(path_raw),
                    old_path=None,
                    added=added,
                    removed=removed,
                    binary=binary,
                )
            )

    if header is not None:
        yield header, changes
