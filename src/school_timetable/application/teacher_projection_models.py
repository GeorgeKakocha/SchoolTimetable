"""Application-owned Teachers page read model (Real-School Setup MVP
Slice B) -- the dedicated projection backing the future School Setup
UI's Teacher list/edit/delete surface.

`TeachersProjectionView` is the sole return shape for
`TeacherProjectionService.project` -- plain, frozen dataclasses built
entirely from an already-loaded `SchedulingProblem` plus the Decision
#35 schedule-exists gate, matching
`teaching_assignments_projection_models.TeachingAssignmentsProjectionView`'s
exact discipline. Never an ORM row, never Pydantic.

`teachers` preserves `SchedulingProblem.teachers`' own tuple order,
which is already persistence ordinal order (`problem_repository.py`
sorts by `ordinal` before construction) -- no further sorting needed
downstream. Deliberately does NOT compute workload, subjects/classes,
or availability here -- those are the concerns of other, separate
projections (Teaching Assignments' own `TeacherWorkload`, a future
Slice); this projection is Teacher identity/display data only.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TeacherProjectionItem:
    id: str
    first_name: str
    last_name: str
    name: str


@dataclass(frozen=True)
class TeachersProjectionView:
    configuration_locked: bool
    teachers: tuple[TeacherProjectionItem, ...]
