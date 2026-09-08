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


class TeachingAssignmentNotFoundError(Exception):
    """No `TeachingRequirement` with this natural ID exists in this
    school/academic-year's persisted configuration (Phase 3C.2,
    `docs/DECISIONS.md` #34) -- a caller-supplied bad/stale natural ID,
    the same kind of "this does not exist" outcome
    `ClassSectionNotFoundError` already represents one level narrower.
    Carries only the natural identifiers already supplied -- no
    persistence surrogate ID."""

    def __init__(
        self, school_natural_id: str, academic_year_natural_id: str, requirement_natural_id: str,
    ) -> None:
        self.school_natural_id = school_natural_id
        self.academic_year_natural_id = academic_year_natural_id
        self.requirement_natural_id = requirement_natural_id
        super().__init__(
            f"no teaching assignment {requirement_natural_id!r} for school={school_natural_id!r}, "
            f"academic_year={academic_year_natural_id!r}"
        )


class UnknownReferenceError(Exception):
    """A `TeachingAssignmentService` create/update request references a
    `teacher_id`/`activity_id`/`participant_group_id` that does not
    exist in this school/academic-year's persisted configuration.
    Distinct from `TeachingAssignmentNotFoundError` (which is about the
    *assignment itself* not existing) -- this is about one of the
    assignment's *referenced* entities not existing. Carries only the
    natural identifiers already supplied."""

    def __init__(
        self, school_natural_id: str, academic_year_natural_id: str, reference_kind: str, reference_id: str,
    ) -> None:
        self.school_natural_id = school_natural_id
        self.academic_year_natural_id = academic_year_natural_id
        self.reference_kind = reference_kind
        self.reference_id = reference_id
        super().__init__(
            f"unknown {reference_kind} {reference_id!r} for school={school_natural_id!r}, "
            f"academic_year={academic_year_natural_id!r}"
        )


class NonWholeClassTargetError(Exception):
    """A `TeachingAssignmentService` create/update request targets a
    `ParticipantGroup` whose `role` is not `WHOLE_CLASS` (Phase 3C.2,
    `docs/DECISIONS.md` #34 -- the narrow first write service only
    manages ordinary whole-class assignments; `SUBGROUP`/
    `MERGED_CLASSES` targets are explicitly out of scope). Carries the
    group's actual role so a caller can explain why, never a
    persistence surrogate ID."""

    def __init__(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        participant_group_id: str,
        actual_role: str,
    ) -> None:
        self.school_natural_id = school_natural_id
        self.academic_year_natural_id = academic_year_natural_id
        self.participant_group_id = participant_group_id
        self.actual_role = actual_role
        super().__init__(
            f"participant group {participant_group_id!r} has role {actual_role!r}, not WHOLE_CLASS, "
            f"for school={school_natural_id!r}, academic_year={academic_year_natural_id!r}"
        )


class AdvancedRequirementNotEditableError(Exception):
    """The `TeachingRequirement` an update/delete request targets is not
    "plain" (Phase 3C.2, `docs/DECISIONS.md` #34) -- it carries at least
    one advanced feature (a non-`WHOLE_CLASS` target, a `split_group_id`,
    a non-`FLEXIBLE` block policy, a non-default distribution policy,
    time preferences, a resource requirement, or a referencing
    `FixedPlacement`) that this narrow write service must never silently
    strip or normalize. `reasons` names every advanced feature found, for
    a precise caller-facing explanation -- never UI wording baked into
    the domain, just the raw feature names."""

    def __init__(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        requirement_natural_id: str,
        reasons: tuple[str, ...],
    ) -> None:
        self.school_natural_id = school_natural_id
        self.academic_year_natural_id = academic_year_natural_id
        self.requirement_natural_id = requirement_natural_id
        self.reasons = reasons
        super().__init__(
            f"teaching assignment {requirement_natural_id!r} is not plain/editable "
            f"(reasons={list(reasons)!r}) for school={school_natural_id!r}, "
            f"academic_year={academic_year_natural_id!r}"
        )


class DuplicateTeachingAssignmentError(Exception):
    """A create/update request would produce a second ordinary
    `TeachingRequirement` sharing the identical `(teacher_id,
    participant_group_id, activity_id)` triple within one academic year
    (Phase 3C.2, `docs/DECISIONS.md` #34 -- an application-level rule,
    deliberately never a blanket database `UNIQUE` constraint). Carries
    only the natural identifiers already supplied."""

    def __init__(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        teacher_id: str,
        participant_group_id: str,
        activity_id: str,
    ) -> None:
        self.school_natural_id = school_natural_id
        self.academic_year_natural_id = academic_year_natural_id
        self.teacher_id = teacher_id
        self.participant_group_id = participant_group_id
        self.activity_id = activity_id
        super().__init__(
            f"a teaching assignment already exists for teacher={teacher_id!r}, "
            f"participant_group={participant_group_id!r}, activity={activity_id!r} "
            f"in school={school_natural_id!r}, academic_year={academic_year_natural_id!r}"
        )


class InvalidTeachingAssignmentError(Exception):
    """A create/update request fails structural validation -- either a
    directly-checked field (e.g. `weekly_periods <= 0`) or a NEW
    preflight-detected structural error the mutation would introduce
    (Phase 3C.2, `docs/DECISIONS.md` #34's save-time validation
    boundary: only errors *newly introduced* by this mutation block the
    write; pre-existing, unrelated configuration problems never do).
    Carries the validator's own safe, structured diagnostics as an
    immutable tuple, exactly like `InvalidSchedulingConfigurationError`
    -- never an ORM/SQLAlchemy object."""

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
            f"invalid teaching assignment for school={school_natural_id!r}, "
            f"academic_year={academic_year_natural_id!r}: {[e.code for e in validation_errors]!r}"
        )


class ConfigurationLockedError(Exception):
    """Scheduling configuration writes are rejected once an
    `AcademicYear` has a generated `Schedule` (`docs/DECISIONS.md` #35).
    Raised identically whether an application-level pre-check found the
    existing `Schedule` or the authoritative, lock-protected recheck
    inside the write transaction found it -- never carries the
    underlying SQLAlchemy exception or any persistence surrogate ID,
    only the natural identifiers the caller already supplied."""

    def __init__(self, school_natural_id: str, academic_year_natural_id: str) -> None:
        self.school_natural_id = school_natural_id
        self.academic_year_natural_id = academic_year_natural_id
        super().__init__(
            f"configuration is locked: a schedule has already been generated for "
            f"school={school_natural_id!r}, academic_year={academic_year_natural_id!r}"
        )


class ConfigurationChangedDuringGenerationError(Exception):
    """Owner Decision #36: immediately before persisting the first
    generated `ScheduleVersion`, `GenerateScheduleService` re-locks the
    `AcademicYear` row, reloads the authoritative current
    `SchedulingProblem`, and compares it against the exact problem the
    solver actually solved. If a configuration write committed in the
    gap between loading and this final recheck, the two differ and this
    error is raised instead of persisting a schedule that no longer
    matches the live configuration -- no `Schedule`/`ScheduleVersion`/
    `ScheduleEntry` row is ever created in this case. The caller may
    simply retry generation against the now-current configuration."""

    def __init__(self, school_natural_id: str, academic_year_natural_id: str) -> None:
        self.school_natural_id = school_natural_id
        self.academic_year_natural_id = academic_year_natural_id
        super().__init__(
            f"configuration changed during generation for school={school_natural_id!r}, "
            f"academic_year={academic_year_natural_id!r}: the loaded configuration no "
            "longer matches the current one; retry generation"
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
