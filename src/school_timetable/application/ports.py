"""Repository ports (Phase 3A2.3, extended Phase 3A3.2): interfaces
`application/` defines and `persistence/` adapters implement, per the
locked ports-and-adapters direction (`docs/ARCHITECTURE.md`,
`docs/DECISIONS.md` #20).

Each `typing.Protocol`, not an ABC -- no inheritance requirement; a
concrete adapter satisfies one purely by matching its method
signatures. `SchedulingProblemRepository` (Phase 3A2.3) loads one
school's one academic year's full configuration.
`ScheduleVersionRepository` (Phase 3A3.2, `docs/DECISIONS.md` #31) reads
and creates the one canonical generated `Schedule`'s versions. Neither
carries generic CRUD (`save`/`create`/`update`/`delete`/`list_*`/`get_*`)
ahead of an actual use case that needs it (Decision #12).
"""
from __future__ import annotations

from typing import Protocol

from school_timetable.application.schedule_models import ActiveScheduleVersion
from school_timetable.domain.problem import SchedulingProblem
from school_timetable.domain.result import ScheduleEntry, SolverStatus


class SchedulingProblemRepository(Protocol):
    def load_by_school_and_year(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
    ) -> SchedulingProblem:
        """Load the complete persisted scheduling configuration for one
        school's one academic year, reconstructed as a frozen
        `SchedulingProblem` with exact tuple order and no persistence
        surrogate ID anywhere in the result.

        Raises `school_timetable.application.errors.
        SchedulingProblemNotFoundError` identically whether
        `school_natural_id` itself is unknown or it is known but
        `academic_year_natural_id` is not -- both are the same
        "this configuration does not exist" outcome to the caller.
        """
        ...


class ScheduleVersionRepository(Protocol):
    """Schedule persistence port (Phase 3A3.2, Decision #31 -- named for
    the operation it actually performs, not a generic
    `ScheduleRepository`). No generic CRUD (`delete`/`update_version`/
    `list_all_versions`/scenario methods) -- none is needed until a
    concrete future use case scopes it (Decision #12)."""

    def get_active_schedule(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
    ) -> ActiveScheduleVersion | None:
        """The active `ScheduleVersion` for this school/year, or `None`
        if no `Schedule` has been generated yet -- that is an expected,
        ordinary outcome, not an error.

        Raises `school_timetable.application.errors.
        SchedulingProblemNotFoundError` (same as `SchedulingProblemRepository`)
        if the school/year itself does not resolve -- distinct from "no
        schedule generated yet for a real school/year", which returns
        `None`.
        """
        ...

    def persist_initial_version(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        entries: tuple[ScheduleEntry, ...],
        solver_status: SolverStatus,
        total_soft_penalty: int,
        wall_time_seconds: float,
        random_seed: int | None,
    ) -> ActiveScheduleVersion:
        """Creates `Schedule` + its first `ScheduleVersion`
        (`version_number=1`, `parent_version_id=None`) and marks it
        active, atomically -- never partially.

        Raises `school_timetable.application.errors.
        ScheduleAlreadyExistsError` if a `Schedule` already exists for
        this school/year (Owner Decision 2), including the losing side
        of a concurrent double-generate race caught via the database's
        `uq_schedule_academic_year_id` constraint. Raises
        `SchedulingProblemNotFoundError` if the school/year itself does
        not resolve.
        """
        ...
