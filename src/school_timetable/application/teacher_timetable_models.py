"""Application-owned teacher-timetable read model (next product slice
after Phase 3C.3, no new phase number -- `docs/DECISIONS.md`'s Owner
Decision 5 exclusion revisited only for the schedule-generation
trigger; this teacher projection mirrors the same architecture without
any new owner decision either).

`TeacherTimetableView` is the sole return shape for
`TeacherTimetableService.project` -- plain, frozen dataclasses built
entirely from already-detached `domain/` objects
(`domain.result.SolverStatus`/`EntrySource`). Never an ORM row, never
Pydantic, and never exposes a persistence surrogate ID or `ordinal`.

Deliberately its own type family, not a reuse of
`class_timetable_models.py`'s types, even where structurally similar
(`DayHeader`) -- each projection's view model stays independently
owned, matching this module's existing convention. `TeacherTimetableEntry`
additionally carries `participant_group_role` and resolved
`class_sections` (`ClassTimetableEntry` never needed either, since a
class view already knows which class it's showing) -- both required so
the frontend can reproduce the WHOLE_CLASS/SUBGROUP/MERGED_CLASSES
display rule already established for Teaching Assignments, without
ever inferring role from name/count.

The grid is already fully ordered and grouped by the time this reaches
`api/serializer.py`: `days` ordered by `Day.index`, `rows` one row per
instructional `Period` ordered by `Period.index`, each row's `cells`
ordered to correspond exactly to `days`. No lunch/break/`block_id`/
`is_instructional` presentation is part of this slice -- deferred,
though the underlying `Period.block_id`/`is_instructional` fields
already exist and require no future schema/solver change to surface.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from school_timetable.domain.result import EntrySource, SolverStatus


@dataclass(frozen=True)
class TeacherTimetableClassSection:
    id: str
    name: str


@dataclass(frozen=True)
class TeacherTimetableEntry:
    source: EntrySource
    activity_id: str
    activity_name: str
    participant_group_id: str | None
    participant_group_name: str | None
    participant_group_role: str | None
    class_sections: tuple[TeacherTimetableClassSection, ...]
    requirement_id: str | None
    reserved_block_id: str | None
    resource_id: str | None


@dataclass(frozen=True)
class DayHeader:
    id: str
    name: str


@dataclass(frozen=True)
class TeacherTimetableCell:
    day_id: str
    entries: tuple[TeacherTimetableEntry, ...]


@dataclass(frozen=True)
class TeacherTimetableRow:
    period_id: str
    period_name: str
    cells: tuple[TeacherTimetableCell, ...]
    """Ordered to correspond exactly to `TeacherTimetableView.days`."""


@dataclass(frozen=True)
class TeacherTimetableView:
    school_id: str
    school_name: str
    academic_year_id: str
    academic_year_label: str
    teacher_id: str
    teacher_name: str
    version_number: int
    solver_status: SolverStatus
    total_soft_penalty: int
    created_at: datetime
    is_active: bool
    days: tuple[DayHeader, ...]
    rows: tuple[TeacherTimetableRow, ...]
