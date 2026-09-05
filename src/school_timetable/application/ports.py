"""Repository ports (Phase 3A2.3): interfaces `application/` defines and
`persistence/` adapters implement, per the locked ports-and-adapters
direction (`docs/ARCHITECTURE.md`, `docs/DECISIONS.md` #20).

A `typing.Protocol`, not an ABC -- no inheritance requirement; a
concrete adapter satisfies this purely by matching the method
signature. Only the first real use case is defined here (load one
school's one academic year's full configuration) -- no generic
CRUD (`save`/`create`/`update`/`delete`/`list_*`/`get_*`) is added
ahead of an actual use case that needs it (Decision #12).
"""
from __future__ import annotations

from typing import Protocol

from school_timetable.domain.problem import SchedulingProblem


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
