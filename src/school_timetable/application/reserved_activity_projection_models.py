"""Application-owned Reserved Activities page read model (Reserved
Activities Slice A2) -- the dedicated, page-scoped projection backing
the future Reserved Activities page's editor surface.

`ReservedActivitiesProjectionView` is the sole return shape for
`ReservedActivityProjectionService.project` -- plain, frozen
dataclasses built entirely from ONE already-loaded `SchedulingProblem`
snapshot plus the Decision #35 schedule-exists gate. Never an ORM row,
never Pydantic.

Every list preserves its own authoritative problem order (already
persistence ordinal/index order): `special_activities` is
`Activity` ordinal order filtered to `ActivityKind.CLUB`; `teachers`/
`class_sections` are their own authoritative `SchedulingProblem` order;
`days`/`periods` are `Day.index`/`Period.index` order (periods include
every period, `is_instructional` and all); `reserved_activities` is
`ReservedBlock` ordinal order, with each item's own
`class_section_ids` in canonical `ClassSection` order and `slots` in
`Day.index`/`Period.index` order.

Deliberately normalized, not denormalized: a `ReservedActivityItem`
carries only natural-ID references (`special_activity_id`,
`class_section_ids`, `teacher_id`), never display names -- a consumer
resolves those against this same view's own top-level catalogs, the
same normalization discipline `special_activities`/`teachers`/
`class_sections` themselves already establish. `ReservedBlock.name` is
never exposed here at all -- it is server-derived, internal
presentation data, never independently meaningful.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ReservedActivitySpecialActivityOption:
    id: str
    name: str


@dataclass(frozen=True)
class ReservedActivityTeacherOption:
    id: str
    name: str


@dataclass(frozen=True)
class ReservedActivityClassSectionOption:
    id: str
    name: str


@dataclass(frozen=True)
class ReservedActivityDayOption:
    id: str
    name: str
    index: int


@dataclass(frozen=True)
class ReservedActivityPeriodOption:
    id: str
    name: str
    index: int
    is_instructional: bool


@dataclass(frozen=True)
class ReservedActivitySlotView:
    day_id: str
    period_id: str


@dataclass(frozen=True)
class ReservedActivityResourceOption:
    """The Resource catalog option list this page's "fixed Resource"
    select needs (Resources B2) -- kept local to this projection
    module, matching how `ReservedActivityTeacherOption`/etc. above are
    already their own local shapes rather than cross-imported from
    other projection modules."""

    id: str
    name: str
    capacity: int


@dataclass(frozen=True)
class ReservedActivityItem:
    id: str
    special_activity_id: str
    class_section_ids: tuple[str, ...]
    teacher_id: str | None
    slots: tuple[ReservedActivitySlotView, ...]
    resource_id: str | None = None


@dataclass(frozen=True)
class ReservedActivitiesProjectionView:
    configuration_locked: bool
    special_activities: tuple[ReservedActivitySpecialActivityOption, ...]
    teachers: tuple[ReservedActivityTeacherOption, ...]
    class_sections: tuple[ReservedActivityClassSectionOption, ...]
    days: tuple[ReservedActivityDayOption, ...]
    periods: tuple[ReservedActivityPeriodOption, ...]
    reserved_activities: tuple[ReservedActivityItem, ...]
    resources: tuple[ReservedActivityResourceOption, ...] = ()
