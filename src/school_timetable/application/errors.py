"""Errors raised across the `application/` repository-port boundary.

Deliberately minimal: one error for one concern (see
`SchedulingProblemRepository` in `ports.py`). Never carries a SQLAlchemy
exception or a persistence surrogate ID -- only the natural/domain
identifiers the caller already supplied.
"""
from __future__ import annotations


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
