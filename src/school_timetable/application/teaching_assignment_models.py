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
    """The owner-approved editable fields (Decision #34, extended by
    Resources B1's Option A) -- the exact input shape for both create
    and update. `resource_id` defaults to `None` so existing Python
    callers constructing this without it keep today's behavior (no
    fixed Resource) unchanged. PUT is full-replacement, never PATCH:
    `resource_id=None` on an update always clears any currently
    assigned Resource -- there is no way to say "leave the Resource
    unchanged" (matching how the other four fields already work)."""

    teacher_id: str
    participant_group_id: str
    activity_id: str
    weekly_periods: int
    resource_id: str | None = None


@dataclass(frozen=True)
class TeachingAssignmentWriteResult:
    """The natural ID of the affected `TeachingRequirement` (backend-
    generated on create, unchanged on update) plus any non-blocking
    preflight warnings the mutation newly introduced (Decision #34's
    save-time validation boundary) -- empty when none."""

    natural_id: str
    warnings: tuple[ValidationError, ...] = ()
