"""Unit tests for the git-log stream parser (``app.ingest.gitlog``).

The byte layout exercised here was verified against real ``git log --no-merges
--numstat -z -M50%`` output during development; these tests pin the format
down so future refactors cannot silently change the parsing rules.
"""
from __future__ import annotations

from io import BytesIO

import pytest

from app.ingest.gitlog import FileChange, iter_tokens, parse_log_stream

RS = b"\x1e"  # record separator before each commit header
US = b"\x1f"  # header field separator
H1 = "a" * 40
H2 = "b" * 40


class SlowStream:
    """BinaryIO wrapper that only ever hands out ``chunk`` bytes per read,
    used to stress-test token framing across read boundaries."""

    def __init__(self, data: bytes, chunk: int = 3) -> None:
        self._buf = BytesIO(data)
        self._chunk = chunk

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            size = self._chunk
        return self._buf.read(min(size, self._chunk))


def header(
    hash_: str,
    *,
    name: str = "Dev One",
    email: str = "dev1@example.com",
    canonical_name: str = "Proper Name",
    canonical_email: str = "proper@example.com",
    ts: int = 1704067200,
    parents: str = "",
    subject: str = "subject",
) -> bytes:
    fields = [hash_, name, email, canonical_name, canonical_email, str(ts), parents, subject]
    return RS + US.join(f.encode() for f in fields) + b"\x00"


def stream(*parts: bytes) -> BytesIO:
    return BytesIO(b"".join(parts))


# ---------------------------------------------------------------------------
# Header parsing
# ---------------------------------------------------------------------------
def test_header_fields_are_parsed():
    (hdr, changes), = parse_log_stream(
        stream(header(H1, ts=1704067201, subject="first commit"), b"\n3\t1\ta.txt\x00")
    )
    assert hdr.hash == H1
    assert (hdr.author_name, hdr.author_email) == ("Dev One", "dev1@example.com")
    assert (hdr.canonical_name, hdr.canonical_email) == ("Proper Name", "proper@example.com")
    assert hdr.ts == 1704067201
    assert hdr.subject == "first commit"
    assert changes == [FileChange(path="a.txt", old_path=None, added=3, removed=1, binary=False)]


def test_root_commit_has_no_parent():
    (hdr, _), = parse_log_stream(stream(header(H1)))
    assert hdr.parent is None


def test_first_parent_of_multi_parent_field_is_used():
    (hdr, _), = parse_log_stream(stream(header(H1, parents=f"{H2} {'c' * 40}")))
    assert hdr.parent == H2


def test_blank_canonical_fields_fall_back_to_raw_identity():
    # No usable mailmap entry: git repeats... nothing, so raw values must win.
    (hdr, _), = parse_log_stream(
        stream(header(H1, canonical_name="", canonical_email=""))
    )
    assert hdr.canonical_name == "Dev One"
    assert hdr.canonical_email == "dev1@example.com"


def test_malformed_header_raises():
    bad = RS + US.join([b"only", b"two"]) + b"\x00"
    with pytest.raises(ValueError):
        list(parse_log_stream(stream(bad)))


def test_unicode_identity_and_path_round_trip():
    (hdr, changes), = parse_log_stream(
        stream(
            header(H1, name="José", email="jose@example.com"),
            b"1\t0\t" + "dir/é.txt".encode("utf-8") + b"\x00",
        )
    )
    assert hdr.author_name == "José"
    assert changes[0].path == "dir/é.txt"


# ---------------------------------------------------------------------------
# Stats records
# ---------------------------------------------------------------------------
def test_binary_record_is_zeroed_and_flagged():
    (_, changes), = parse_log_stream(stream(header(H1), b"-\t-\tbin.dat\x00"))
    assert changes == [FileChange(path="bin.dat", old_path=None, added=0, removed=0, binary=True)]


def test_rename_record_uses_old_then_new_path_tokens():
    (_, changes), = parse_log_stream(
        stream(header(H1), b"0\t0\t\x00old/name.txt\x00new/name.txt\x00")
    )
    assert changes == [
        FileChange(path="new/name.txt", old_path="old/name.txt", added=0, removed=0, binary=False)
    ]


def test_rename_with_edits_keeps_counts_on_new_path():
    (_, changes), = parse_log_stream(
        stream(header(H1), b"4\t2\t\x00old.txt\x00new.txt\x00")
    )
    assert changes[0].path == "new.txt"
    assert changes[0].old_path == "old.txt"
    assert (changes[0].added, changes[0].removed) == (4, 2)


def test_only_first_stats_record_may_carry_a_leading_newline():
    (_, changes), = parse_log_stream(
        stream(header(H1), b"\n1\t0\ta.txt\x00", b"2\t0\tb.txt\x00")
    )
    assert [c.path for c in changes] == ["a.txt", "b.txt"]
    assert [c.added for c in changes] == [1, 2]


def test_empty_commit_yields_header_without_changes():
    commits = list(parse_log_stream(stream(header(H1), header(H2, subject="empty"))))
    assert [c[0].subject for c in commits] == ["subject", "empty"]
    assert commits[1][1] == []


def test_multiple_commits_with_mixed_records():
    data = b"".join(
        [
            header(H1, subject="one"),
            b"2\t0\tx.txt\x00",
            header(H2, subject="two", parents=H1),
            b"0\t0\t\x00a.txt\x00b.txt\x00",
            b"-\t-\timg.png\x00",
        ]
    )
    commits = list(parse_log_stream(stream(data)))
    assert [(h.subject, h.hash) for h, _ in commits] == [("one", H1), ("two", H2)]
    assert commits[0][1] == [FileChange("x.txt", None, 2, 0, False)]
    assert commits[1][1] == [
        FileChange("b.txt", "a.txt", 0, 0, False),
        FileChange("img.png", None, 0, 0, True),
    ]


def test_stray_empty_tokens_are_ignored():
    (_, changes), = parse_log_stream(stream(header(H1), b"\x00", b"1\t0\ta.txt\x00", b"\x00"))
    assert [c.path for c in changes] == ["a.txt"]


# ---------------------------------------------------------------------------
# Streaming / framing
# ---------------------------------------------------------------------------
def test_iter_tokens_without_trailing_nul():
    data = b"\x00".join([b"alpha", b"beta", b"gamma"])
    assert list(iter_tokens(BytesIO(data))) == [b"alpha", b"beta", b"gamma"]


def test_iter_tokens_across_tiny_reads():
    tokens = [b"alpha", b"beta", b"gamma", b"delta"]
    data = b"\x00".join(tokens)
    assert list(iter_tokens(SlowStream(data, 1), 16)) == tokens


def test_parse_stream_across_tiny_reads():
    data = b"".join(
        [
            header(H1, subject="one"),
            b"2\t0\tx.txt\x00",
            header(H2, subject="two"),
            b"0\t0\t\x00a.txt\x00b.txt\x00",
            b"-\t-\tbin.dat\x00",
        ]
    )
    commits = list(parse_log_stream(SlowStream(data, 7)))
    assert [h.hash for h, _ in commits] == [H1, H2]
    assert commits[0][1][0].path == "x.txt"
    assert (commits[1][1][0].old_path, commits[1][1][0].path) == ("a.txt", "b.txt")
    assert commits[1][1][1].binary is True
