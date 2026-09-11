"""Application-owned Calendar read model (Calendar A) -- the dedicated
projection backing the future School Setup "Calendar & Bell Schedule"
tab's list/edit/reorder surface, and `GET .../calendar` directly.

`CalendarProjectionView` is the sole return shape for
`CalendarProjectionService.project` -- plain, frozen dataclasses built
entirely from an already-loaded `SchedulingProblem` plus the Decision
#35 schedule-exists gate, matching `ResourcesProjectionView`'s exact
discipline. Never an ORM row, never Pydantic.

Raw `block_id` is never exposed here -- `starts_new_block` is the
public abstraction (derived by `domain.calendar.derive_starts_new_block`).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import time


@dataclass(frozen=True)
class CalendarDayItem:
    id: str
    name: str
    index: int


@dataclass(frozen=True)
class CalendarPeriodItem:
    id: str
    name: str
    index: int
    start_time: time | None
    end_time: time | None
    starts_new_block: bool
    is_instructional: bool


@dataclass(frozen=True)
class CalendarProjectionView:
    configuration_locked: bool
    days: tuple[CalendarDayItem, ...]
    periods: tuple[CalendarPeriodItem, ...]
