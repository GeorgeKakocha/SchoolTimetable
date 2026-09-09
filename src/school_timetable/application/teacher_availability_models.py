"""Plain, application-owned request/result shapes for the Teacher
Availability write use case (Owner Decision #38) -- never an ORM row,
never Pydantic, matching `teacher_models.py`'s exact discipline.

The write unit is the *complete desired sparse exception set* for one
Teacher, never a single-cell CRUD row: a caller submits every
`(day_id, period_id)` cell it wants to be a `PREFER_NOT`/`UNAVAILABLE`
exception, and the repository reconciles the persisted rows for that
Teacher to match exactly. `AVAILABLE` is deliberately never a valid
member of this set -- it is represented by a cell's *absence*, per the
existing sparse-default-AVAILABLE contract (`ProblemIndex.get_availability`)
this slice preserves unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TeacherAvailabilityExceptionFields:
    """One requested exception cell. `status` stays a plain `str` here
    (never the domain `AvailabilityStatus` enum) -- this is the
    framework-free application-input boundary; `teacher_availability_rules`
    is responsible for validating it against the enum's real members."""

    day_id: str
    period_id: str
    status: str


@dataclass(frozen=True)
class TeacherAvailabilityReplaceFields:
    """The complete desired exception set for one Teacher -- an empty
    tuple means "clear every explicit exception for this Teacher"."""

    exceptions: tuple[TeacherAvailabilityExceptionFields, ...]


@dataclass(frozen=True)
class TeacherAvailabilityExceptionResult:
    day_id: str
    period_id: str
    status: str


@dataclass(frozen=True)
class TeacherAvailabilityWriteResult:
    """The written Teacher's own resolved, authoritative exception set
    after reconciliation -- never the whole page projection (mirrors
    `TeacherWriteResult`'s "hand back the written row's own state, let
    the caller re-fetch the page projection for page-level state"
    discipline)."""

    teacher_id: str
    exceptions: tuple[TeacherAvailabilityExceptionResult, ...]
