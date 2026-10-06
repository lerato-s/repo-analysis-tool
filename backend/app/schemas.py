"""Request models for the REST API (responses are plain dicts)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Mode = Literal["all", "range", "list"]
Granularity = Literal["auto", "day", "week", "month"]


class CloneRequest(BaseModel):
    url: str = Field(min_length=1, max_length=2048)
    name: str | None = Field(default=None, max_length=80)


class FilterBody(BaseModel):
    """The dashboard's commit-set / author / object filter selection."""

    mode: Mode = "all"
    from_ts: int | None = None  # inclusive
    to_ts: int | None = None  # exclusive
    hashes: list[str] = Field(default_factory=list)  # mode='list'
    author_keys: list[int] = Field(default_factory=list)  # effective group keys
    path: str = ""  # object scope: file or directory path
    granularity: Granularity = "auto"


class MergeRequest(BaseModel):
    label: str = Field(min_length=1, max_length=120)
    members: list[tuple[str, str]] = Field(min_length=2)  # [name, email] pairs
