"""Application-owned Teaching Assignments page read model (Phase 3C.2b).

`TeachingAssignmentsProjectionView` is the sole return shape for
`TeachingAssignmentsProjectionService.project` -- plain, frozen
dataclasses built entirely from an already-loaded `SchedulingProblem`
plus the Decision #35 schedule-exists gate. Never an ORM row, never
Pydantic, matching the same discipline `class_timetable_models.
ClassTimetableView` already applies.

Every list here is already fully ordered, grouped, and name-resolved by
the time this reaches `api/serializer.py`: `assignments` preserves
`SchedulingProblem.teaching_requirements`' own tuple order,
`teachers`/`teacher_workloads` preserve `.teachers`' order,
`whole_class_targets` preserves `.class_sections`' order, `activities`
preserves `.activities`' order -- a consumer (the API layer, and
eventually React) needs to do no further sorting, grouping, or
ID-to-name resolution, and must never infer which `ParticipantGroup` is
a class's canonical `WHOLE_CLASS` group itself.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TeachingAssignmentClassSection:
    id: str
    name: str


@dataclass(frozen=True)
class TeachingAssignmentItem:
    """One `TeachingRequirement`, projected for display -- ALL
    requirements are represented here, plain and advanced alike
    (Decision #34/#36's write scope never hides an advanced row; it only
    makes it read-only)."""

    id: str
    teacher_id: str
    teacher_name: str
    activity_id: str
    activity_name: str
    participant_group_id: str
    participant_group_name: str
    participant_group_role: str
    class_sections: tuple[TeachingAssignmentClassSection, ...]
    weekly_periods: int
    editable: bool
    advanced_reasons: tuple[str, ...]


@dataclass(frozen=True)
class TeacherOption:
    id: str
    name: str


@dataclass(frozen=True)
class ActivityOption:
    id: str
    name: str


@dataclass(frozen=True)
class WholeClassTarget:
    """The authoritative, backend-owned mapping from a visible
    `ClassSection` to its canonical `WHOLE_CLASS` `ParticipantGroup` --
    the exact `participant_group_id` a create/update request must
    submit. A `ClassSection` with zero or more than one `WHOLE_CLASS`
    group (a broken Decision #33 invariant) is simply omitted here; this
    projection never guesses, and is not `preflight`'s enforcement
    point for that invariant."""

    class_section_id: str
    class_section_name: str
    participant_group_id: str
    participant_group_name: str


@dataclass(frozen=True)
class TeacherWorkload:
    """`total_weekly_periods` sums EVERY `TeachingRequirement` assigned
    to this teacher -- plain and advanced, `WHOLE_CLASS`/`SUBGROUP`/
    `MERGED_CLASSES` alike -- never only the editable subset. A teacher
    with zero requirements still appears, at `0`."""

    teacher_id: str
    teacher_name: str
    total_weekly_periods: int


@dataclass(frozen=True)
class TeachingAssignmentsProjectionView:
    configuration_locked: bool
    assignments: tuple[TeachingAssignmentItem, ...]
    teachers: tuple[TeacherOption, ...]
    whole_class_targets: tuple[WholeClassTarget, ...]
    activities: tuple[ActivityOption, ...]
    teacher_workloads: tuple[TeacherWorkload, ...]
