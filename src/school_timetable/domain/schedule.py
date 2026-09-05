"""Schedule domain types (Phase 2C).

A ``Schedule`` is no longer just transient solver output once a school
starts editing an existing generated timetable -- it is an in-memory
object a human can move lessons in, lock, and re-optimize around.

Kept deliberately minimal and persistence-free: no database, no versioning
history beyond "the previous schedule the caller happens to pass in" as an
explicit re-optimization argument. A dedicated ``ScheduleVersion`` type was
considered and is not introduced -- at this phase, "the reference schedule"
is just the ``Schedule`` value the caller holds before calling
``reoptimize``; there is no benefit yet to wrapping it further.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field

from school_timetable.domain.result import ScheduleEntry


@dataclass(frozen=True)
class OccurrenceKey:
    """Identifies one logical lesson occurrence for locking purposes: a
    ``TeachingRequirement``'s block of periods on one specific day,
    anchored at that block's first (lowest-index) period.

    ``anchor_period_id`` is required, not optional: only REQUIRED blocks
    and a formed PREFERRED double are guaranteed by the CP-SAT model to be
    the *only* thing that requirement does that day (both have an explicit
    day-cap constraint). FLEXIBLE requirements have no such guarantee and
    routinely place several, often non-adjacent, periods for the same
    requirement on the same day -- so ``(requirement_id, day_id)`` alone
    would be ambiguous between several genuinely independent occurrences.
    The anchor period disambiguates uniformly across every block policy;
    see ``scheduling/editing.py`` for how it is derived from a schedule.
    """

    requirement_id: str
    day_id: str
    anchor_period_id: str


@dataclass(frozen=True)
class Schedule:
    """An in-memory timetable snapshot: the entries plus which logical
    occurrences are currently locked.

    Locking is schedule state, not a ``TeachingRequirement`` property --
    the same requirement's Monday occurrence might be locked while its
    Wednesday occurrence is not. A ``FixedPlacement`` (school-configured,
    on the ``SchedulingProblem`` itself) is independent of this and always
    HARD regardless of lock state -- see ``docs/SCHEDULE_EDITING.md``.
    """

    entries: tuple[ScheduleEntry, ...]
    locked_occurrences: frozenset[OccurrenceKey] = field(default_factory=frozenset)

    def is_locked(self, key: OccurrenceKey) -> bool:
        return key in self.locked_occurrences

    def with_entries(self, entries: tuple[ScheduleEntry, ...]) -> "Schedule":
        return dataclasses.replace(self, entries=entries)

    def with_locked(self, locked_occurrences: frozenset[OccurrenceKey]) -> "Schedule":
        return dataclasses.replace(self, locked_occurrences=locked_occurrences)
