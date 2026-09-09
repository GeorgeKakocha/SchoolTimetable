"""Plain, application-owned request/result shapes for the Real-School
Setup MVP Slice B Teacher write use case -- never an ORM row, never
Pydantic, matching `teaching_assignment_models.py`'s exact discipline.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TeacherFields:
    """The two owner-approved editable fields (Owner Decision #37) --
    the exact input shape for both create and update."""

    first_name: str
    last_name: str


@dataclass(frozen=True)
class TeacherWriteResult:
    """The written `Teacher`'s own resolved fields -- `id` is the
    natural ID (backend-generated on create, unchanged on update),
    `first_name`/`last_name` are the normalized (trimmed) values
    actually persisted, and `name` is `Teacher.full_name`. Unlike
    `TeachingAssignmentWriteResult`, a Teacher write affects nothing
    but its own row, so handing back the full written representation
    is the natural minimal result -- not a page projection, no
    aggregate-refresh machinery invented."""

    id: str
    first_name: str
    last_name: str
    name: str
