"""Plain, application-owned request/result shapes for Phase 3C.2's
narrow Teaching Assignment write service (`docs/DECISIONS.md` #34) --
never an ORM row, never Pydantic, matching the same discipline
`schedule_models.ActiveScheduleVersion` already applies.
"""
from __future__ import annotations

from dataclasses import dataclass

from school_timetable.validation.errors import ValidationError


@dataclass(frozen=True)
class TeachingAssignmentFields:
    """The four owner-approved editable fields (Decision #34) -- the
    exact input shape for both create and update."""

    teacher_id: str
    participant_group_id: str
    activity_id: str
    weekly_periods: int


@dataclass(frozen=True)
class TeachingAssignmentWriteResult:
    """The natural ID of the affected `TeachingRequirement` (backend-
    generated on create, unchanged on update) plus any non-blocking
    preflight warnings the mutation newly introduced (Decision #34's
    save-time validation boundary) -- empty when none."""

    natural_id: str
    warnings: tuple[ValidationError, ...] = ()
