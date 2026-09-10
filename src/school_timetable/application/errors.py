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


class NonOrdinaryActivityTargetError(Exception):
    """A `TeachingAssignmentService` create/update request targets an
    `Activity` whose `kind` is not `ORDINARY` (pre-Slice-D correction:
    `ActivityKind.CLUB` is scheduled via `ReservedBlock`, never via a
    `TeachingRequirement` -- see `domain/activities.py`). Mirrors
    `NonWholeClassTargetError`'s exact pattern: the `Activity` genuinely
    exists (this is not `UnknownReferenceError`), it is simply invalid
    for this narrow write surface. Carries the activity's actual kind
    so a caller can explain why, never a persistence surrogate ID."""

    def __init__(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        activity_id: str,
        actual_kind: str,
    ) -> None:
        self.school_natural_id = school_natural_id
        self.academic_year_natural_id = academic_year_natural_id
        self.activity_id = activity_id
        self.actual_kind = actual_kind
        super().__init__(
            f"activity {activity_id!r} has kind {actual_kind!r}, not ORDINARY, "
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


class TeacherNotFoundError(Exception):
    """The requested `teacher_id` does not exist in this school/
    academic-year's persisted configuration. Mirrors
    `ClassSectionNotFoundError`'s narrow style exactly: a caller-supplied
    bad natural ID within an otherwise-valid school/year scope, one
    level narrower than `SchedulingProblemNotFoundError`. Distinct from
    "no schedule generated yet" (`TeacherTimetableService.project`
    returning `None`), which is an ordinary application state, not an
    error. Never used for a malformed stored reference *inside* an
    already-loaded entry -- that is a genuine internal/configuration
    defect and must fail loudly/generically (a raw `KeyError`), never
    silently invent a label. Carries only the natural identifiers
    already supplied -- no persistence surrogate ID."""

    def __init__(
        self, school_natural_id: str, academic_year_natural_id: str, teacher_id: str,
    ) -> None:
        self.school_natural_id = school_natural_id
        self.academic_year_natural_id = academic_year_natural_id
        self.teacher_id = teacher_id
        super().__init__(
            f"no teacher {teacher_id!r} for school={school_natural_id!r}, "
            f"academic_year={academic_year_natural_id!r}"
        )


class InvalidTeacherError(Exception):
    """A `TeacherService` create/update request fails input validation
    (Real-School Setup MVP Slice B) -- a blank `first_name`/`last_name`
    after trimming. Carries the validator's own safe, structured
    diagnostics as an immutable tuple, exactly like
    `InvalidTeachingAssignmentError` -- never an ORM/SQLAlchemy object."""

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
            f"invalid teacher for school={school_natural_id!r}, "
            f"academic_year={academic_year_natural_id!r}: {[e.code for e in validation_errors]!r}"
        )


class InvalidClassError(Exception):
    """A `ClassSectionService` create/update request fails input
    validation (Real-School Setup MVP Slice C) -- a blank `name` after
    trimming. Carries the validator's own safe, structured diagnostics
    as an immutable tuple, exactly like `InvalidTeacherError` -- never
    an ORM/SQLAlchemy object."""

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
            f"invalid class for school={school_natural_id!r}, "
            f"academic_year={academic_year_natural_id!r}: {[e.code for e in validation_errors]!r}"
        )


class DuplicateClassError(Exception):
    """A `ClassSectionService` create/update request would produce a
    second `ClassSection` sharing the identical (exact, case-sensitive,
    trimmed) `name` within one academic year (Real-School Setup MVP
    Slice C -- an application-level rule, deliberately never a blanket
    database `UNIQUE` constraint, mirroring
    `DuplicateTeachingAssignmentError`'s existing precedent). Carries
    only the natural identifiers already supplied."""

    def __init__(self, school_natural_id: str, academic_year_natural_id: str, name: str) -> None:
        self.school_natural_id = school_natural_id
        self.academic_year_natural_id = academic_year_natural_id
        self.name = name
        super().__init__(
            f"a class named {name!r} already exists for school={school_natural_id!r}, "
            f"academic_year={academic_year_natural_id!r}"
        )


class ClassSectionInUseError(Exception):
    """A `ClassSectionService.delete` request targets a `ClassSection`
    currently referenced by the persisted scheduling configuration
    (Real-School Setup MVP Slice C) -- never cascade-deleted.
    `referenced_by` names every referencing kind found, in the
    deterministic order `TEACHING_REQUIREMENT`, `RESERVED_BLOCK`,
    `SUBGROUP`, `MERGED_CLASSES` (only the kinds that actually
    reference this class), never a persistence surrogate ID."""

    def __init__(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        class_id: str,
        referenced_by: tuple[str, ...],
    ) -> None:
        self.school_natural_id = school_natural_id
        self.academic_year_natural_id = academic_year_natural_id
        self.class_id = class_id
        self.referenced_by = referenced_by
        super().__init__(
            f"class {class_id!r} is still referenced by {list(referenced_by)!r} "
            f"for school={school_natural_id!r}, academic_year={academic_year_natural_id!r}"
        )


class TeacherInUseError(Exception):
    """A `TeacherService.delete` request targets a `Teacher` currently
    referenced by the persisted scheduling configuration (Real-School
    Setup MVP Slice B) -- never cascade-deleted. `referenced_by` names
    every referencing entity kind found, in the deterministic order
    `TEACHING_REQUIREMENT`, `TEACHER_AVAILABILITY`, `RESERVED_BLOCK`
    (only the kinds that actually reference this teacher), never a
    persistence surrogate ID."""

    def __init__(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        teacher_id: str,
        referenced_by: tuple[str, ...],
    ) -> None:
        self.school_natural_id = school_natural_id
        self.academic_year_natural_id = academic_year_natural_id
        self.teacher_id = teacher_id
        self.referenced_by = referenced_by
        super().__init__(
            f"teacher {teacher_id!r} is still referenced by {list(referenced_by)!r} "
            f"for school={school_natural_id!r}, academic_year={academic_year_natural_id!r}"
        )


class InvalidSubjectError(Exception):
    """A `SubjectService` create/update request fails input validation
    (Real-School Setup MVP Slice D -- "Subject" is the user-facing name
    for `Activity(kind=ORDINARY)`, never a separate domain entity) -- a
    blank `name` after trimming. Carries the validator's own safe,
    structured diagnostics as an immutable tuple, exactly like
    `InvalidClassError`."""

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
            f"invalid subject for school={school_natural_id!r}, "
            f"academic_year={academic_year_natural_id!r}: {[e.code for e in validation_errors]!r}"
        )


class DuplicateSubjectError(Exception):
    """A `SubjectService` create/update request would produce a second
    `Activity(kind=ORDINARY)` sharing the identical (exact,
    case-sensitive, trimmed) `name` within one academic year (Real-School
    Setup MVP Slice D). Checked ONLY against other `ORDINARY` activities
    -- a `CLUB` activity sharing the same name is never a conflict.
    Deliberately never a blanket database `UNIQUE` constraint, mirroring
    `DuplicateClassError`'s existing precedent."""

    def __init__(self, school_natural_id: str, academic_year_natural_id: str, name: str) -> None:
        self.school_natural_id = school_natural_id
        self.academic_year_natural_id = academic_year_natural_id
        self.name = name
        super().__init__(
            f"a subject named {name!r} already exists for school={school_natural_id!r}, "
            f"academic_year={academic_year_natural_id!r}"
        )


class SubjectNotFoundError(Exception):
    """No `ActivityKind.ORDINARY` activity with this natural ID exists
    in this school/academic-year's persisted configuration (Real-School
    Setup MVP Slice D). Raised identically whether `activity_id` does
    not exist at all, or it exists but is `ActivityKind.CLUB` -- a CLUB
    activity is indistinguishable from a missing Subject through this
    filtered resource surface, so `ActivityKind` is never leaked here.
    Carries only the natural identifiers already supplied -- no
    persistence surrogate ID."""

    def __init__(
        self, school_natural_id: str, academic_year_natural_id: str, subject_id: str,
    ) -> None:
        self.school_natural_id = school_natural_id
        self.academic_year_natural_id = academic_year_natural_id
        self.subject_id = subject_id
        super().__init__(
            f"no subject {subject_id!r} for school={school_natural_id!r}, "
            f"academic_year={academic_year_natural_id!r}"
        )


class SubjectInUseError(Exception):
    """A `SubjectService.delete` request targets an
    `Activity(kind=ORDINARY)` currently referenced by the persisted
    scheduling configuration (Real-School Setup MVP Slice D) -- never
    cascade-deleted. `referenced_by` names every referencing entity kind
    found, in the deterministic order `TEACHING_REQUIREMENT`,
    `RESERVED_BLOCK` (only the kinds that actually reference this
    subject), never a persistence surrogate ID."""

    def __init__(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        subject_id: str,
        referenced_by: tuple[str, ...],
    ) -> None:
        self.school_natural_id = school_natural_id
        self.academic_year_natural_id = academic_year_natural_id
        self.subject_id = subject_id
        self.referenced_by = referenced_by
        super().__init__(
            f"subject {subject_id!r} is still referenced by {list(referenced_by)!r} "
            f"for school={school_natural_id!r}, academic_year={academic_year_natural_id!r}"
        )


class InvalidTeacherAvailabilityError(Exception):
    """A `TeacherAvailabilityService.replace_exceptions` request fails
    input validation (Teacher Availability, Owner Decision #38) --
    an explicit `AVAILABLE` entry in the requested exception set (the
    sparse contract requires it to be omitted, never stated), a
    duplicate `(day_id, period_id)` cell within one request, or an
    unrecognized status string. Carries the validator's own safe,
    structured diagnostics as an immutable tuple, exactly like
    `InvalidTeacherError`/`InvalidClassError`/`InvalidSubjectError` --
    never an ORM/SQLAlchemy object."""

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
            f"invalid teacher availability replacement for school={school_natural_id!r}, "
            f"academic_year={academic_year_natural_id!r}: {[e.code for e in validation_errors]!r}"
        )


class InvalidSpecialActivityError(Exception):
    """A `SpecialActivityService` create/update request fails input
    validation (Reserved Activities Slice A1 -- "Special Activity" is
    the user-facing name for `Activity(kind=CLUB)`, never a separate
    domain entity, mirroring "Subject"'s own relationship to
    `Activity(kind=ORDINARY)`) -- a blank `name` after trimming.
    Carries the validator's own safe, structured diagnostics as an
    immutable tuple, exactly like `InvalidSubjectError`."""

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
            f"invalid special activity for school={school_natural_id!r}, "
            f"academic_year={academic_year_natural_id!r}: {[e.code for e in validation_errors]!r}"
        )


class DuplicateSpecialActivityError(Exception):
    """A `SpecialActivityService` create/update request would produce a
    second `Activity(kind=CLUB)` sharing the identical (exact,
    case-sensitive, trimmed) `name` within one academic year (Reserved
    Activities Slice A1). Checked ONLY against other `CLUB` activities
    -- an `ORDINARY` Subject sharing the same name is never a conflict,
    mirroring `DuplicateSubjectError`'s own symmetric exclusion."""

    def __init__(self, school_natural_id: str, academic_year_natural_id: str, name: str) -> None:
        self.school_natural_id = school_natural_id
        self.academic_year_natural_id = academic_year_natural_id
        self.name = name
        super().__init__(
            f"a special activity named {name!r} already exists for school={school_natural_id!r}, "
            f"academic_year={academic_year_natural_id!r}"
        )


class SpecialActivityNotFoundError(Exception):
    """No `ActivityKind.CLUB` activity with this natural ID exists in
    this school/academic-year's persisted configuration (Reserved
    Activities Slice A1). Raised identically whether `activity_id`
    does not exist at all, or it exists but is `ActivityKind.ORDINARY`
    -- an ORDINARY Subject is indistinguishable from a missing Special
    Activity through this filtered resource surface, so `ActivityKind`
    is never leaked here. Carries only the natural identifiers already
    supplied -- no persistence surrogate ID."""

    def __init__(
        self, school_natural_id: str, academic_year_natural_id: str, special_activity_id: str,
    ) -> None:
        self.school_natural_id = school_natural_id
        self.academic_year_natural_id = academic_year_natural_id
        self.special_activity_id = special_activity_id
        super().__init__(
            f"no special activity {special_activity_id!r} for school={school_natural_id!r}, "
            f"academic_year={academic_year_natural_id!r}"
        )


class SpecialActivityInUseError(Exception):
    """A `SpecialActivityService.delete` request targets an
    `Activity(kind=CLUB)` currently referenced by a persisted
    `ReservedBlock` (Reserved Activities Slice A1) -- never
    cascade-deleted. `referenced_by` names every referencing entity
    kind found (only ever `RESERVED_BLOCK` -- a CLUB activity is never
    a `TeachingRequirement` target), never a persistence surrogate ID."""

    def __init__(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        special_activity_id: str,
        referenced_by: tuple[str, ...],
    ) -> None:
        self.school_natural_id = school_natural_id
        self.academic_year_natural_id = academic_year_natural_id
        self.special_activity_id = special_activity_id
        self.referenced_by = referenced_by
        super().__init__(
            f"special activity {special_activity_id!r} is still referenced by {list(referenced_by)!r} "
            f"for school={school_natural_id!r}, academic_year={academic_year_natural_id!r}"
        )


class InvalidReservedActivityError(Exception):
    """A `ReservedActivityService` create/update request fails
    structural or semantic validation (Reserved Activities Slice A2) --
    an empty `class_section_ids`/`slots` collection, an in-request
    duplicate class or slot, a non-instructional slot, or a slot where
    the attached Teacher is `UNAVAILABLE`, or a cross-block class/
    teacher slot collision. Carries the validator's own safe,
    structured diagnostics as an immutable tuple, exactly like
    `InvalidSubjectError`/`InvalidTeacherAvailabilityError`. Never
    carries `UNKNOWN_REFERENCE`-shaped or wrong-Special-Activity-kind
    diagnostics -- those are always the dedicated `UnknownReferenceError`/
    `NonSpecialActivityTargetError` instead."""

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
            f"invalid reserved activity for school={school_natural_id!r}, "
            f"academic_year={academic_year_natural_id!r}: {[e.code for e in validation_errors]!r}"
        )


class ReservedActivityNotFoundError(Exception):
    """No `ReservedBlock` with this natural ID exists in this
    school/academic-year's persisted configuration (Reserved
    Activities Slice A2). Distinct from a nested reference not
    resolving (`UnknownReferenceError`) -- this is about the top-level
    PUT/DELETE *target* itself not existing. Carries only the natural
    identifiers already supplied -- no persistence surrogate ID."""

    def __init__(
        self, school_natural_id: str, academic_year_natural_id: str, reserved_activity_id: str,
    ) -> None:
        self.school_natural_id = school_natural_id
        self.academic_year_natural_id = academic_year_natural_id
        self.reserved_activity_id = reserved_activity_id
        super().__init__(
            f"no reserved activity {reserved_activity_id!r} for school={school_natural_id!r}, "
            f"academic_year={academic_year_natural_id!r}"
        )


class NonSpecialActivityTargetError(Exception):
    """A `ReservedActivityService` create/update request's nested
    `special_activity_id` resolves to an `Activity` whose `kind` is not
    `CLUB` (Reserved Activities Slice A2) -- the mirror image of
    `NonOrdinaryActivityTargetError`, but deliberately a SEPARATE error
    class: the dedicated Reserved Activity API must never leak raw
    `ActivityKind`/`CLUB`/`ORDINARY` vocabulary in its own responses
    (unlike Teaching Assignments' existing, unrelated
    `NON_ORDINARY_ACTIVITY_TARGET` contract, which this does not reuse
    or alter). Carries only the natural `activity_id` -- never the
    actual kind, never a persistence surrogate ID."""

    def __init__(
        self, school_natural_id: str, academic_year_natural_id: str, activity_id: str,
    ) -> None:
        self.school_natural_id = school_natural_id
        self.academic_year_natural_id = academic_year_natural_id
        self.activity_id = activity_id
        super().__init__(
            f"activity {activity_id!r} is not a special activity, "
            f"for school={school_natural_id!r}, academic_year={academic_year_natural_id!r}"
        )
