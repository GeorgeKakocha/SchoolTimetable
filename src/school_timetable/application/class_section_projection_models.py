"""Application-owned Classes page read model (Real-School Setup MVP
Slice C) -- the dedicated projection backing the future School Setup
UI's Class list/edit/delete surface.

`ClassSectionsProjectionView` is the sole return shape for
`ClassSectionProjectionService.project` -- plain, frozen dataclasses
built entirely from an already-loaded `SchedulingProblem` plus the
Decision #35 schedule-exists gate, matching
`teacher_projection_models.TeachersProjectionView`'s exact discipline.
Never an ORM row, never Pydantic.

`classes` preserves `SchedulingProblem.class_sections`' own tuple
order, which is already persistence ordinal order
(`problem_repository.py` sorts by `ordinal` before construction) -- no
further sorting needed downstream. Deliberately does NOT expose the
canonical WHOLE_CLASS `ParticipantGroup` id, role, or membership --
that remains internal to this admin surface (Owner Decision #33); it
also never computes subjects, teachers, workload, or student count.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ClassSectionProjectionItem:
    id: str
    name: str


@dataclass(frozen=True)
class ClassSectionsProjectionView:
    configuration_locked: bool
    classes: tuple[ClassSectionProjectionItem, ...]
