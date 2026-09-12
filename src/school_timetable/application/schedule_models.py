"""Application-owned schedule read model (Phase 3A3.2, `docs/DECISIONS.md`
#31's `ScheduleVersionRepository` design).

`ActiveScheduleVersion` is the sole return shape for
`application.ports.ScheduleVersionRepository` -- a plain, frozen
dataclass built entirely from already-detached `domain/` objects
(`domain.result.ScheduleEntry`/`SolverStatus`,
`domain.schedule.OccurrenceKey`). Never an ORM row, never Pydantic, and
never exposes a persistence surrogate ID (`schedule.id`,
`schedule_version.id`, `schedule_entry.ordinal`) -- only the public,
natural/domain fields Decision #31 lists.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from school_timetable.domain.result import ScheduleEntry, SolverStatus
from school_timetable.domain.schedule import OccurrenceKey


@dataclass(frozen=True)
class ActiveScheduleVersion:
    version_number: int
    solver_status: SolverStatus
    total_soft_penalty: int
    wall_time_seconds: float
    random_seed: int | None
    created_at: datetime
    entries: tuple[ScheduleEntry, ...]
    locked_occurrences: frozenset[OccurrenceKey]
    configuration_revision_number: int
    """Safe Configuration Changes, Slice A: the natural `revision_number`
    (never a persistence surrogate ID) of the `ConfigurationRevision`
    this version's entries were generated/edited against -- a caller
    resolving the `SchedulingProblem` this schedule is valid against
    must use `SchedulingProblemRepository.load_for_revision` with THIS
    value, never `load_by_school_and_year`'s own "whatever is currently
    relevant" resolution."""


@dataclass(frozen=True)
class ScheduleVersionSummary:
    """One row of `ScheduleVersionRepository.list_versions`'s history
    listing -- version metadata only, never `entries`/`locked_occurrences`
    (a caller wanting a specific version's full state calls `get_version`
    instead). `parent_version_number` is `None` only for the very first
    version of a `Schedule` (`persist_initial_version`'s
    `parent_version_id=None`) -- every other version was created from
    some prior active version and always carries one."""

    version_number: int
    created_at: datetime
    solver_status: SolverStatus
    total_soft_penalty: int
    is_active: bool
    parent_version_number: int | None


@dataclass(frozen=True)
class ScheduleVersionSnapshot:
    """`ScheduleVersionRepository.get_version`'s return shape: a specific
    (not necessarily active) `ScheduleVersion`'s full state -- the same
    fields `ActiveScheduleVersion` carries, plus `is_active`/
    `parent_version_number` so a caller never needs a second repository
    call just to learn whether the version it asked for happens to be
    the active one. Used for historical class/teacher timetable
    projection and as a restore command's copy source."""

    version_number: int
    solver_status: SolverStatus
    total_soft_penalty: int
    wall_time_seconds: float
    random_seed: int | None
    created_at: datetime
    is_active: bool
    parent_version_number: int | None
    configuration_revision_number: int
    """See `ActiveScheduleVersion.configuration_revision_number` --
    identical meaning. Historical class/teacher timetable projection
    must resolve `SchedulingProblem` from THIS value via `load_for_
    revision`, never from whatever is currently published/draft."""
    entries: tuple[ScheduleEntry, ...]
    locked_occurrences: frozenset[OccurrenceKey]
