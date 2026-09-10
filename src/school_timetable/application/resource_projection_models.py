"""Application-owned Resources catalog read model (Resources Slice A)
-- the dedicated projection backing the future School Setup "Rooms &
Resources" tab's list/edit/delete surface.

`ResourcesProjectionView` is the sole return shape for
`ResourceProjectionService.project` -- plain, frozen dataclasses built
entirely from an already-loaded `SchedulingProblem` plus the Decision
#35 schedule-exists gate, matching
`special_activity_projection_models.SpecialActivitiesProjectionView`'s
exact discipline. Never an ORM row, never Pydantic.

`resources` preserves `SchedulingProblem.resources`' own tuple order
(already persistence ordinal order) -- this projection never re-sorts.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ResourceProjectionItem:
    id: str
    name: str
    capacity: int


@dataclass(frozen=True)
class ResourcesProjectionView:
    configuration_locked: bool
    resources: tuple[ResourceProjectionItem, ...]
