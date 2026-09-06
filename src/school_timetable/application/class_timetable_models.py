"""Application-owned class-timetable read model (Phase 3B.1,
`docs/DECISIONS.md` #32).

`ClassTimetableView` is the sole return shape for
`ClassTimetableService.project` -- plain, frozen dataclasses built
entirely from already-detached `domain/` objects
(`domain.result.SolverStatus`/`EntrySource`). Never an ORM row, never
Pydantic, and never exposes a persistence surrogate ID or `ordinal` --
only the public, natural/domain fields Decision #32 lists.

The grid is already fully ordered and grouped by the time this reaches
`api/serializer.py`: `days` is ordered by `Day.index`, `rows` is one row
per instructional `Period` ordered by `Period.index`, and each row's
`cells` are ordered to correspond exactly to `days`. A consumer (the API
layer, and eventually React) needs to do no further sorting, grouping,
or ID-to-name resolution.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from school_timetable.domain.result import EntrySource, SolverStatus


@dataclass(frozen=True)
class ClassTimetableEntry:
    source: EntrySource
    activity_id: str
    activity_name: str
    teacher_id: str | None
    teacher_name: str | None
    participant_group_id: str | None
    participant_group_name: str | None
    requirement_id: str | None
    reserved_block_id: str | None
    resource_id: str | None


@dataclass(frozen=True)
class DayHeader:
    id: str
    name: str


@dataclass(frozen=True)
class ClassTimetableCell:
    day_id: str
    entries: tuple[ClassTimetableEntry, ...]


@dataclass(frozen=True)
class ClassTimetableRow:
    period_id: str
    period_name: str
    cells: tuple[ClassTimetableCell, ...]
    """Ordered to correspond exactly to `ClassTimetableView.days`."""


@dataclass(frozen=True)
class ClassTimetableView:
    school_id: str
    school_name: str
    academic_year_id: str
    academic_year_label: str
    class_section_id: str
    class_section_name: str
    version_number: int
    solver_status: SolverStatus
    total_soft_penalty: int
    created_at: datetime
    is_active: bool
    days: tuple[DayHeader, ...]
    rows: tuple[ClassTimetableRow, ...]
