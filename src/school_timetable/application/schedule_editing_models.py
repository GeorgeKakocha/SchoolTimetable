"""Application-owned read model for `ScheduleEditingService.preview_move`
(manual timetable editing: move-target preview slice). Plain, frozen
dataclasses built entirely from `scheduling.editing.MoveViolation`
values -- never an ORM row, never Pydantic, never a persistence
surrogate ID, matching `schedule_models.ActiveScheduleVersion`'s own
discipline.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MovePreviewTarget:
    """One candidate destination slot's `validate_move` outcome for the
    previewed source occurrence -- `violations` is exactly
    `MoveValidationResult.violations` as `(code, message)` pairs, the
    same shape `MoveNotAllowedError` already carries for the real move
    command, so the API layer maps both through one shared response
    type."""

    day_id: str
    period_id: str
    allowed: bool
    violations: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class MovePreviewResult:
    version_number: int
    targets: tuple[MovePreviewTarget, ...]
