"""Application-owned Teacher Availability page read model (Owner
Decision #38) -- the dedicated projection backing the future Teacher
Availability UI's matrix surface.

`TeacherAvailabilityProjectionView` is the sole return shape for
`TeacherAvailabilityProjectionService.project` -- plain, frozen
dataclasses built entirely from an already-loaded `SchedulingProblem`
plus the Decision #35 schedule-exists gate, matching
`TeachersProjectionView`'s exact discipline. Never an ORM row, never
Pydantic.

`teachers`/`days`/`periods` all preserve `SchedulingProblem`'s own
existing tuple order (`problem_repository.py` already sorts each by
its own authoritative ordinal/index before construction) -- never
re-sorted (e.g. alphabetically) here. `exceptions` is a *sparse
exception* projection only -- `AVAILABLE` rows are never projected,
whether they arise from the sparse default (no row at all) or, in
principle, an explicit legacy `AVAILABLE` row somehow persisted; the
UI is expected to synthesize `AVAILABLE` from a cell's absence, per
the existing `ProblemIndex.get_availability` contract this projection
does not alter."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TeacherAvailabilityTeacherItem:
    id: str
    name: str


@dataclass(frozen=True)
class TeacherAvailabilityDayItem:
    id: str
    name: str
    index: int


@dataclass(frozen=True)
class TeacherAvailabilityPeriodItem:
    id: str
    name: str
    index: int
    block_id: str
    is_instructional: bool


@dataclass(frozen=True)
class TeacherAvailabilityExceptionItem:
    teacher_id: str
    day_id: str
    period_id: str
    status: str


@dataclass(frozen=True)
class TeacherAvailabilityProjectionView:
    configuration_locked: bool
    teachers: tuple[TeacherAvailabilityTeacherItem, ...]
    days: tuple[TeacherAvailabilityDayItem, ...]
    periods: tuple[TeacherAvailabilityPeriodItem, ...]
    exceptions: tuple[TeacherAvailabilityExceptionItem, ...]
