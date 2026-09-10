"""Plain, application-owned request/result shapes for the Reserved
Activities Slice A1 Special Activity write use case -- never an ORM
row, never Pydantic, matching `subject_models.py`'s exact discipline.

A "Special Activity" is not a new domain entity -- it is the
user-facing name for `Activity(kind=CLUB)` (see `domain/activities.py`).
These models never carry `kind`: the write surface always
creates/targets `CLUB` activities only, so the field would be
redundant, constant dead weight.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SpecialActivityFields:
    """The one owner-approved editable field -- the exact input shape
    for both create and update."""

    name: str


@dataclass(frozen=True)
class SpecialActivityWriteResult:
    """The written `Activity`'s own resolved fields (`kind` is always
    `CLUB` and is never exposed here)."""

    id: str
    name: str
