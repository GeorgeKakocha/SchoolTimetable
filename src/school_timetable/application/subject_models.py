"""Plain, application-owned request/result shapes for the Real-School
Setup MVP Slice D Subject write use case -- never an ORM row, never
Pydantic, matching `class_section_models.py`'s exact discipline.

A "Subject" is not a new domain entity -- it is the user-facing name
for `Activity(kind=ORDINARY)` (see `domain/activities.py`). These
models never carry `kind`: the write surface always creates/targets
`ORDINARY` activities only, so the field would be redundant, constant
dead weight.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SubjectFields:
    """The one owner-approved editable field -- the exact input shape
    for both create and update."""

    name: str


@dataclass(frozen=True)
class SubjectWriteResult:
    """The written `Activity`'s own resolved fields (`kind` is always
    `ORDINARY` and is never exposed here)."""

    id: str
    name: str
