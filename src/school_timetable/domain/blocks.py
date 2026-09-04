"""Reserved/club blocks and fixed placements.

Both concepts pre-determine part of the timetable outside of CP-SAT's
control: a ``ReservedBlock`` reserves specific slots for specific classes
(e.g. a club), and a ``FixedPlacement`` pins one lesson of a
``TeachingRequirement`` to a specific slot.
"""
from __future__ import annotations

from dataclasses import dataclass

from school_timetable.domain.calendar import TimeSlot


@dataclass(frozen=True)
class ReservedBlock:
    """A recurring club/reserved activity that occupies specific classes at
    specific slots. Ordinary teaching requirements are forbidden from using
    these (class, slot) combinations; the reserved activity itself
    occupies them instead.
    """

    id: str
    name: str
    activity_id: str
    class_sections: tuple[str, ...]
    slots: tuple[TimeSlot, ...]
    teacher_id: str | None = None


@dataclass(frozen=True)
class FixedPlacement:
    """Pins one lesson of a TeachingRequirement to an exact slot."""

    id: str
    requirement_id: str
    slot: TimeSlot
