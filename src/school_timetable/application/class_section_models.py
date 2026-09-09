"""Plain, application-owned request/result shapes for the Real-School
Setup MVP Slice C Class write use case -- never an ORM row, never
Pydantic, matching `teacher_models.py`'s exact discipline.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ClassSectionFields:
    """The one owner-approved editable field -- the exact input shape
    for both create and update. The canonical WHOLE_CLASS group is
    never part of this input; it is entirely internal (Owner Decision
    #33)."""

    name: str


@dataclass(frozen=True)
class ClassSectionWriteResult:
    """The written `ClassSection`'s own resolved fields. Never exposes
    the canonical group's ID -- it remains an internal scheduling
    implementation detail, not part of the ordinary Class write
    response."""

    id: str
    name: str
