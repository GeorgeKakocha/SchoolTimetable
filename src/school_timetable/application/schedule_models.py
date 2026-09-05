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
