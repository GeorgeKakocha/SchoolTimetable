"""Errors raised across the `application/` repository-port boundary and
by `GenerateScheduleService` (Phase 3A3.3, `docs/DECISIONS.md` #31) and
`ClassTimetableService` (Phase 3B.1, `docs/DECISIONS.md` #32).

Deliberately minimal, one error per distinct expected outcome a caller
(eventually an HTTP layer, Phase 3A3.4) must branch on -- never a
SQLAlchemy exception, ORM object, SQL text, or persistence surrogate ID,
only the natural/domain identifiers and safe diagnostics the caller
already supplied or the validator/solver already produced. Internal
defects (an unexpected solver error, an independent-verifier rejection
of a solver-claimed-successful result) are deliberately NOT added here
-- they are never a public application-level outcome and never get an
HTTP mapping, so they stay as plain internal exceptions local to
`generate_schedule_service.py`, the module that raises them.
"""
from __future__ import annotations

from school_timetable.validation.errors import ValidationError


class SchedulingProblemNotFoundError(Exception):
    """No scheduling configuration exists for this school/academic-year
    pair. Raised identically whether the school itself is unknown or
    the school is known but the academic year is not -- from the
    caller's perspective both are the same "this configuration does not
    exist" outcome, and the natural IDs alone are enough to say so."""

    def __init__(self, school_natural_id: str, academic_year_natural_id: str) -> None:
        self.school_natural_id = school_natural_id
        self.academic_year_natural_id = academic_year_natural_id
        super().__init__(
            f"no scheduling configuration for school={school_natural_id!r}, "
            f"academic_year={academic_year_natural_id!r}"
        )


class ScheduleAlreadyExistsError(Exception):
    """A canonical `Schedule` already exists for this school/academic-year
    pair (`docs/DECISIONS.md` #31, Owner Decision 2 -- generation is
    initial-generation-only). Raised identically whether an
    application-level pre-check found the existing `Schedule` or the
    database's `uq_schedule_academic_year_id` constraint rejected the
    losing side of a concurrent double-generate race -- never carries the
    underlying SQLAlchemy exception, SQL text, or any persistence
    surrogate ID, only the natural identifiers the caller already
    supplied."""

    def __init__(self, school_natural_id: str, academic_year_natural_id: str) -> None:
        self.school_natural_id = school_natural_id
        self.academic_year_natural_id = academic_year_natural_id
        super().__init__(
            f"a schedule already exists for school={school_natural_id!r}, "
            f"academic_year={academic_year_natural_id!r}"
        )


class InvalidSchedulingConfigurationError(Exception):
    """The persisted scheduling configuration itself fails preflight
    validation (`validation.preflight.run_preflight`) -- structurally
    loadable but semantically invalid, so generation never reaches the
    solver. Carries the validator's own safe, structured diagnostics
    (`ValidationError`: `code`, `message`, a plain-value `context` dict)
    as an immutable tuple -- never an ORM/SQLAlchemy object, never a
    persistence surrogate ID."""

    def __init__(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        validation_errors: tuple[ValidationError, ...],
    ) -> None:
        self.school_natural_id = school_natural_id
        self.academic_year_natural_id = academic_year_natural_id
        self.validation_errors = validation_errors
        super().__init__(
            f"invalid scheduling configuration for school={school_natural_id!r}, "
            f"academic_year={academic_year_natural_id!r}: "
            f"{[e.code for e in validation_errors]!r}"
        )


class ScheduleInfeasibleError(Exception):
    """The scheduling configuration is valid but its current HARD
    constraints admit no feasible timetable -- CP-SAT proved
    `SolverStatus.INFEASIBLE`. An expected, real outcome, not a defect.
    Carries only the natural school/year IDs -- `SchedulingResult` has
    no further safe per-problem diagnostics for this status, so none
    are invented here."""

    def __init__(self, school_natural_id: str, academic_year_natural_id: str) -> None:
        self.school_natural_id = school_natural_id
        self.academic_year_natural_id = academic_year_natural_id
        super().__init__(
            f"no feasible schedule exists for school={school_natural_id!r}, "
            f"academic_year={academic_year_natural_id!r}"
        )


class ClassSectionNotFoundError(Exception):
    """The requested `class_section_id` does not exist in this school/
    academic-year's persisted configuration (`docs/DECISIONS.md` #32).
    A caller-supplied bad natural ID within an otherwise-valid
    school/year scope -- the same kind of "this does not exist" outcome
    `SchedulingProblemNotFoundError` already represents for school/year,
    just one level narrower. Distinct from "no schedule generated yet"
    (`ClassTimetableService.project` returning `None`), which is an
    ordinary application state, not an error. Carries only the natural
    identifiers already supplied -- no persistence surrogate ID."""

    def __init__(
        self, school_natural_id: str, academic_year_natural_id: str, class_section_id: str,
    ) -> None:
        self.school_natural_id = school_natural_id
        self.academic_year_natural_id = academic_year_natural_id
        self.class_section_id = class_section_id
        super().__init__(
            f"no class section {class_section_id!r} for school={school_natural_id!r}, "
            f"academic_year={academic_year_natural_id!r}"
        )
