"""Application-owned Special Activities catalog read model (Reserved
Activities Slice A1) -- the dedicated projection backing the future
School Setup UI's Special Activity list/edit/delete surface.

`SpecialActivitiesProjectionView` is the sole return shape for
`SpecialActivityProjectionService.project` -- plain, frozen dataclasses
built entirely from an already-loaded `SchedulingProblem` plus the
Decision #35 schedule-exists gate, matching
`subject_projection_models.SubjectsProjectionView`'s exact discipline.
Never an ORM row, never Pydantic.

`special_activities` preserves `SchedulingProblem.activities`' own
tuple order (already persistence ordinal order), filtered to
`ActivityKind.CLUB` -- `ORDINARY` activities are never members of this
projection at all, not merely hidden. Deliberately does NOT expose
`kind` -- every member here is already, by construction, a Special
Activity.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SpecialActivityProjectionItem:
    id: str
    name: str


@dataclass(frozen=True)
class SpecialActivitiesProjectionView:
    configuration_locked: bool
    special_activities: tuple[SpecialActivityProjectionItem, ...]
