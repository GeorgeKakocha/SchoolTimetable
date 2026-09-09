"""Application-owned Subjects page read model (Real-School Setup MVP
Slice D) -- the dedicated projection backing the future School Setup
UI's Subject list/edit/delete surface.

`SubjectsProjectionView` is the sole return shape for
`SubjectProjectionService.project` -- plain, frozen dataclasses built
entirely from an already-loaded `SchedulingProblem` plus the Decision
#35 schedule-exists gate, matching
`class_section_projection_models.ClassSectionsProjectionView`'s exact
discipline. Never an ORM row, never Pydantic.

`subjects` preserves `SchedulingProblem.activities`' own tuple order
(already persistence ordinal order), filtered to `ActivityKind.ORDINARY`
-- `CLUB` activities are never members of this projection at all, not
merely hidden. Deliberately does NOT expose `kind` -- every member here
is already, by construction, a Subject.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SubjectProjectionItem:
    id: str
    name: str


@dataclass(frozen=True)
class SubjectsProjectionView:
    configuration_locked: bool
    subjects: tuple[SubjectProjectionItem, ...]
