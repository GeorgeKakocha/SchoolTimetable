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

from school_timetable.scheduling.lock_compatibility import IncompatibleLock
from school_timetable.validation.errors import ValidationError


class InvalidSchoolProvisioningError(Exception):
    """The provisioning command contains blank normalized fields."""

    def __init__(self, validation_errors: tuple[ValidationError, ...]) -> None:
        self.validation_errors = validation_errors
        super().__init__(f"invalid school provisioning command: {[e.code for e in validation_errors]!r}")


class SchoolProvisioningConflictError(Exception):
    """A School public ID already belongs to an incompatible aggregate."""

    def __init__(self, school_natural_id: str) -> None:
        self.school_natural_id = school_natural_id
        super().__init__(f"school public ID {school_natural_id!r} already exists with incompatible data")


class AcademicYearProvisioningConflictError(Exception):
    """An AcademicYear public identity conflicts within its School."""

    def __init__(self, school_natural_id: str, academic_year_natural_id: str) -> None:
        self.school_natural_id = school_natural_id
        self.academic_year_natural_id = academic_year_natural_id
        super().__init__(
            f"academic-year public ID {academic_year_natural_id!r} for school="
            f"{school_natural_id!r} already exists with incompatible data"
        )


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


class StaleScheduleVersionError(Exception):
    """The manual-editing/re-optimization persistence sibling of
    `ConfigurationChangedDuringGenerationError`: `persist_edited_version`
    re-locks the `AcademicYear` row and reloads the *actual* current
    active `ScheduleVersion` immediately before persisting a candidate
    edited/re-optimized `Schedule`. If its `version_number` no longer
    matches the `base_version_number` the caller edited/re-optimized
    from -- another edit/re-optimization was promoted to active first --
    this error is raised instead of persisting, and no `ScheduleVersion`,
    `ScheduleEntry`, or `LockedOccurrence` row is ever created;
    `Schedule.active_version_id` is left untouched. Carries only the
    natural school/year IDs plus both version numbers, never a
    persistence surrogate ID or the underlying SQLAlchemy exception. The
    caller may reload the now-current active version and retry its
    edit/re-optimization against it."""

    def __init__(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        expected_base_version_number: int,
        actual_active_version_number: int,
    ) -> None:
        self.school_natural_id = school_natural_id
        self.academic_year_natural_id = academic_year_natural_id
        self.expected_base_version_number = expected_base_version_number
        self.actual_active_version_number = actual_active_version_number
        super().__init__(
            f"stale schedule version for school={school_natural_id!r}, "
            f"academic_year={academic_year_natural_id!r}: expected active version "
            f"{expected_base_version_number!r} but the current active version is "
            f"{actual_active_version_number!r}; reload and retry"
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


class InvalidResourceError(Exception):
    """A `ResourceService` create/update request fails input validation
    (Resources Slice A) -- a blank `name` after trimming, or a
    `capacity` that is not a positive integer. Carries the validator's
    own safe, structured diagnostics as an immutable tuple, exactly
    like `InvalidSpecialActivityError`/`InvalidSubjectError`."""

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
            f"invalid resource for school={school_natural_id!r}, "
            f"academic_year={academic_year_natural_id!r}: {[e.code for e in validation_errors]!r}"
        )


class DuplicateResourceError(Exception):
    """A `ResourceService` create/update request would produce a second
    `Resource` sharing the identical (exact, case-sensitive, trimmed)
    `name` within one academic year (Resources Slice A) -- mirrors
    `DuplicateSpecialActivityError`'s own single-catalog-scoped
    exclusion; an identically-named Subject/Teacher/Class/Special
    Activity is never a conflict, since Resource is its own separate
    catalog."""

    def __init__(self, school_natural_id: str, academic_year_natural_id: str, name: str) -> None:
        self.school_natural_id = school_natural_id
        self.academic_year_natural_id = academic_year_natural_id
        self.name = name
        super().__init__(
            f"a resource named {name!r} already exists for school={school_natural_id!r}, "
            f"academic_year={academic_year_natural_id!r}"
        )


class ResourceNotFoundError(Exception):
    """No `Resource` with this natural ID exists in this
    school/academic-year's persisted configuration (Resources Slice A).
    Carries only the natural identifiers already supplied -- no
    persistence surrogate ID."""

    def __init__(
        self, school_natural_id: str, academic_year_natural_id: str, resource_id: str,
    ) -> None:
        self.school_natural_id = school_natural_id
        self.academic_year_natural_id = academic_year_natural_id
        self.resource_id = resource_id
        super().__init__(
            f"no resource {resource_id!r} for school={school_natural_id!r}, "
            f"academic_year={academic_year_natural_id!r}"
        )


class ResourceInUseError(Exception):
    """A `ResourceService.delete` request targets a `Resource` currently
    referenced by a persisted `TeachingRequirement.resource_id`
    (Resources Slice A) -- never cascade-deleted. `referenced_by` names
    every referencing entity kind found (only ever
    `TEACHING_REQUIREMENT` in Slice A -- `ReservedBlock` has no
    `resource_id` yet; a future Resources B slice will extend this),
    never a persistence surrogate ID."""

    def __init__(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        resource_id: str,
        referenced_by: tuple[str, ...],
    ) -> None:
        self.school_natural_id = school_natural_id
        self.academic_year_natural_id = academic_year_natural_id
        self.resource_id = resource_id
        self.referenced_by = referenced_by
        super().__init__(
            f"resource {resource_id!r} is still referenced by {list(referenced_by)!r} "
            f"for school={school_natural_id!r}, academic_year={academic_year_natural_id!r}"
        )


class InvalidDayError(Exception):
    """A `CalendarService` Day create/update/delete/move request fails
    input validation (Calendar A) -- a blank `name`, the final Day
    (`NO_CALENDAR_DAYS`), or a move request at the top/bottom edge.
    Carries the validator's own safe, structured diagnostics as an
    immutable tuple, exactly like `InvalidResourceError`."""

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
            f"invalid day operation for school={school_natural_id!r}, "
            f"academic_year={academic_year_natural_id!r}: {[e.code for e in validation_errors]!r}"
        )


class DuplicateDayError(Exception):
    """A `CalendarService` Day create/update request would produce a
    second Day sharing the identical (exact, case-sensitive, trimmed)
    `name` within one academic year (Calendar A)."""

    def __init__(self, school_natural_id: str, academic_year_natural_id: str, name: str) -> None:
        self.school_natural_id = school_natural_id
        self.academic_year_natural_id = academic_year_natural_id
        self.name = name
        super().__init__(
            f"a day named {name!r} already exists for school={school_natural_id!r}, "
            f"academic_year={academic_year_natural_id!r}"
        )


class DayNotFoundError(Exception):
    """No Day with this natural ID exists in this school/academic-year's
    persisted configuration (Calendar A). Carries only the natural
    identifiers already supplied -- no persistence surrogate ID."""

    def __init__(self, school_natural_id: str, academic_year_natural_id: str, day_id: str) -> None:
        self.school_natural_id = school_natural_id
        self.academic_year_natural_id = academic_year_natural_id
        self.day_id = day_id
        super().__init__(
            f"no day {day_id!r} for school={school_natural_id!r}, "
            f"academic_year={academic_year_natural_id!r}"
        )


class DayInUseError(Exception):
    """A `CalendarService.day_delete` request targets a Day currently
    referenced by a persisted `TeacherAvailability`, `ReservedBlock`
    slot, or `FixedPlacement` (Calendar A) -- never cascade-deleted.
    `referenced_by` names every referencing entity kind found, never a
    persistence surrogate ID. Historical `ScheduleEntry`/
    `LockedOccurrence` references are never checked here -- once any
    Schedule exists, every Calendar write is already rejected by
    `ConfigurationLockedError` (Decision #35/#36), so this check can
    never even be reached in that state."""

    def __init__(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        day_id: str,
        referenced_by: tuple[str, ...],
    ) -> None:
        self.school_natural_id = school_natural_id
        self.academic_year_natural_id = academic_year_natural_id
        self.day_id = day_id
        self.referenced_by = referenced_by
        super().__init__(
            f"day {day_id!r} is still referenced by {list(referenced_by)!r} "
            f"for school={school_natural_id!r}, academic_year={academic_year_natural_id!r}"
        )


class InvalidPeriodError(Exception):
    """A `CalendarService` Period create/update/delete/move request
    fails input validation (Calendar A) -- a blank `name`, an
    only-one-of `start_time`/`end_time`, `start_time >= end_time`, a
    clock-time overlap with a neighboring Period, the final
    instructional Period (`NO_INSTRUCTIONAL_PERIODS`), or a move
    request at the top/bottom edge. Carries the validator's own safe,
    structured diagnostics as an immutable tuple, exactly like
    `InvalidResourceError`."""

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
            f"invalid period operation for school={school_natural_id!r}, "
            f"academic_year={academic_year_natural_id!r}: {[e.code for e in validation_errors]!r}"
        )


class DuplicatePeriodError(Exception):
    """A `CalendarService` Period create/update request would produce a
    second Period sharing the identical (exact, case-sensitive,
    trimmed) `name` within one academic year (Calendar A)."""

    def __init__(self, school_natural_id: str, academic_year_natural_id: str, name: str) -> None:
        self.school_natural_id = school_natural_id
        self.academic_year_natural_id = academic_year_natural_id
        self.name = name
        super().__init__(
            f"a period named {name!r} already exists for school={school_natural_id!r}, "
            f"academic_year={academic_year_natural_id!r}"
        )


class PeriodNotFoundError(Exception):
    """No Period with this natural ID exists in this school/academic-
    year's persisted configuration (Calendar A). Carries only the
    natural identifiers already supplied -- no persistence surrogate
    ID."""

    def __init__(self, school_natural_id: str, academic_year_natural_id: str, period_id: str) -> None:
        self.school_natural_id = school_natural_id
        self.academic_year_natural_id = academic_year_natural_id
        self.period_id = period_id
        super().__init__(
            f"no period {period_id!r} for school={school_natural_id!r}, "
            f"academic_year={academic_year_natural_id!r}"
        )


class PeriodInUseError(Exception):
    """A `CalendarService.period_delete` request targets a Period
    currently referenced by a persisted `TeacherAvailability`,
    `ReservedBlock` slot, or `FixedPlacement` (`referenced_by` includes
    the matching entries among `TEACHER_AVAILABILITY`/`RESERVED_BLOCK`/
    `FIXED_PLACEMENT`) -- OR by a `TeachingRequirement.time_preferences`
    entry whose `preferred_period_indexes` would silently change
    meaning if this delete proceeded (`referenced_by` then includes
    `TIME_PREFERENCE`; see `calendar_rules.validate_period_delete`'s
    own docstring for the exact index-drift-safety rule). Never
    cascade-deleted. Historical `ScheduleEntry`/`LockedOccurrence`
    references are never checked here, for the same reason
    `DayInUseError` never checks them."""

    def __init__(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        period_id: str,
        referenced_by: tuple[str, ...],
    ) -> None:
        self.school_natural_id = school_natural_id
        self.academic_year_natural_id = academic_year_natural_id
        self.period_id = period_id
        self.referenced_by = referenced_by
        super().__init__(
            f"period {period_id!r} is still referenced by {list(referenced_by)!r} "
            f"for school={school_natural_id!r}, academic_year={academic_year_natural_id!r}"
        )


class PeriodReorderBlockedError(Exception):
    """A `CalendarService.period_move` request is rejected outright
    (Calendar A, section 12's TimePreference index-drift safety rule)
    because at least one `TeachingRequirement` in this academic year
    carries a `TimePreference`/`preferred_period_indexes` entry --
    `TimePreference` stores raw `Period.index` integers, not `Period.id`
    references, so *any* reorder could silently redirect an existing
    preference to a different Period with no structural error. A blanket
    block (rather than a precise reachability check, which `period_delete`
    does use) is the deliberately simpler, safer rule for reorder,
    matching the task's own explicit instruction."""

    def __init__(self, school_natural_id: str, academic_year_natural_id: str, period_id: str) -> None:
        self.school_natural_id = school_natural_id
        self.academic_year_natural_id = academic_year_natural_id
        self.period_id = period_id
        super().__init__(
            f"period {period_id!r} cannot be reordered while teaching requirements contain time "
            f"preferences, for school={school_natural_id!r}, academic_year={academic_year_natural_id!r}"
        )


# == Manual timetable editing (ScheduleEditingService) =======================


class NoActiveScheduleError(Exception):
    """A move/lock/unlock/reoptimize command was issued for a school/year
    that has no active `Schedule` at all yet -- distinct from
    `ScheduleInfeasibleError` (a schedule was attempted and failed) and
    from `persistence.schedule_repository.CorruptScheduleStateError`
    (persisted state exists but is malformed). There is nothing to edit
    until `POST .../schedule/generate` succeeds at least once."""

    def __init__(self, school_natural_id: str, academic_year_natural_id: str) -> None:
        self.school_natural_id = school_natural_id
        self.academic_year_natural_id = academic_year_natural_id
        super().__init__(
            f"no active schedule exists yet for school={school_natural_id!r}, "
            f"academic_year={academic_year_natural_id!r} to edit; generate one first"
        )


class MoveNotAllowedError(Exception):
    """`scheduling.editing.validate_move` rejected a well-formed manual
    move request -- a HARD-rule violation (locked/fixed occurrence,
    conflicting target, teacher/group/resource conflict, etc.), not a
    malformed request. Carries every `MoveViolation` as `(code, message)`
    pairs, in order, so a caller can preserve the specific reason rather
    than flattening it to a generic rejection -- never a persistence
    surrogate ID or SQLAlchemy detail."""

    def __init__(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        violations: tuple[tuple[str, str], ...],
    ) -> None:
        self.school_natural_id = school_natural_id
        self.academic_year_natural_id = academic_year_natural_id
        self.violations = violations
        reasons = "; ".join(f"{code}: {message}" for code, message in violations)
        super().__init__(
            f"move not allowed for school={school_natural_id!r}, "
            f"academic_year={academic_year_natural_id!r}: {reasons}"
        )


class InvalidEditTargetError(Exception):
    """A move/lock/unlock request named a (requirement, day, period) that
    `scheduling.editing` could not even resolve into a valid logical
    occurrence to act on -- an unknown requirement, or a schedule that is
    not itself HARD-valid (`scheduling.editing.EditingError`, whose own
    `code` is carried through verbatim: e.g. `OCCURRENCE_NOT_FOUND`,
    `INVALID_SCHEDULE`). Distinct from `MoveNotAllowedError`, which is a
    well-formed request a HARD rule rejects."""

    def __init__(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        code: str | None,
        message: str,
    ) -> None:
        self.school_natural_id = school_natural_id
        self.academic_year_natural_id = academic_year_natural_id
        self.code = code
        super().__init__(
            f"invalid edit target for school={school_natural_id!r}, "
            f"academic_year={academic_year_natural_id!r}: {message}"
        )


class ReoptimizationInfeasibleError(Exception):
    """`scheduling.reoptimize.reoptimize` proved `SolverStatus.INFEASIBLE`
    for the current problem plus the active schedule's locked occurrences
    -- e.g. a lock now directly contradicts a new HARD condition. Nothing
    is persisted; the active version is left exactly as it was. Carries
    only the natural school/year IDs."""

    def __init__(self, school_natural_id: str, academic_year_natural_id: str) -> None:
        self.school_natural_id = school_natural_id
        self.academic_year_natural_id = academic_year_natural_id
        super().__init__(
            f"no feasible re-optimized schedule exists for school={school_natural_id!r}, "
            f"academic_year={academic_year_natural_id!r} given the current locks/configuration"
        )


class ReoptimizationInvalidInputError(Exception):
    """`scheduling.reoptimize.reoptimize` returned `SolverStatus.
    INVALID_INPUT` -- either the current `SchedulingProblem` itself fails
    preflight, or the active version's own entries fail the reference
    schedule's structural/topology check (`_reference_schedule_
    structural_violations`) -- both mean re-optimization could not even
    be attempted. Nothing is persisted. Carries the same safe, structured
    `ValidationError` diagnostics `InvalidSchedulingConfigurationError`
    does."""

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
            f"re-optimization input invalid for school={school_natural_id!r}, "
            f"academic_year={academic_year_natural_id!r}: "
            f"{[e.code for e in validation_errors]!r}"
        )


class ScheduleVersionNotFoundError(Exception):
    """A specific `version_number` was requested (historical class/
    teacher timetable projection, or a restore command's source) but no
    such `ScheduleVersion` exists for this school/year's `Schedule`.
    Distinct from `NoActiveScheduleError`/`ScheduleVersionRepository.
    get_version`/`list_versions` returning `None` (no `Schedule` at all
    exists yet) -- this is the narrower "the container exists, this
    specific item inside it does not" outcome, matching
    `ClassSectionNotFoundError`/`TeacherNotFoundError`'s own style.
    Never silently falls back to the active version. Carries only
    natural IDs, never a persistence surrogate."""

    def __init__(
        self, school_natural_id: str, academic_year_natural_id: str, version_number: int,
    ) -> None:
        self.school_natural_id = school_natural_id
        self.academic_year_natural_id = academic_year_natural_id
        self.version_number = version_number
        super().__init__(
            f"no ScheduleVersion {version_number!r} exists for school={school_natural_id!r}, "
            f"academic_year={academic_year_natural_id!r}"
        )


class VersionAlreadyActiveError(Exception):
    """`ScheduleEditingService.restore` was asked to restore the
    already-active version -- a pointless operation that would create an
    identical duplicate `ScheduleVersion` for no reason. Nothing is
    persisted; the active version is left exactly as it was. Carries
    only natural IDs."""

    def __init__(
        self, school_natural_id: str, academic_year_natural_id: str, version_number: int,
    ) -> None:
        self.school_natural_id = school_natural_id
        self.academic_year_natural_id = academic_year_natural_id
        self.version_number = version_number
        super().__init__(
            f"ScheduleVersion {version_number!r} is already active for school="
            f"{school_natural_id!r}, academic_year={academic_year_natural_id!r}; nothing to restore"
        )


class RestoreVerificationFailedError(Exception):
    """Defense-in-depth (`ScheduleEditingService.restore`): the
    historical source version's own entries failed the independent
    verifier when checked against the CURRENT `SchedulingProblem`.
    Expected never to happen in ordinary operation -- the configuration
    a restored version's entries depend on is not expected to change --
    but restoring is refused rather than silently persisting a
    known-invalid schedule as the new active version. Nothing is
    persisted; the active version is left exactly as it was. Carries
    only natural IDs."""

    def __init__(
        self, school_natural_id: str, academic_year_natural_id: str, version_number: int,
    ) -> None:
        self.school_natural_id = school_natural_id
        self.academic_year_natural_id = academic_year_natural_id
        self.version_number = version_number
        super().__init__(
            f"historical ScheduleVersion {version_number!r} failed independent verification "
            f"against the current configuration for school={school_natural_id!r}, "
            f"academic_year={academic_year_natural_id!r}; restore refused"
        )


class NoConfigurationDraftError(Exception):
    """Safe Configuration Changes, Slice B: `discard_draft` (or any
    future draft-only command) was called for a school/year with no
    open `ConfigurationRevision` draft. Never silently a no-op --
    surfaced as a clear, non-2xx application error rather than
    performing zero writes and returning success. Carries only natural
    IDs."""

    def __init__(self, school_natural_id: str, academic_year_natural_id: str) -> None:
        self.school_natural_id = school_natural_id
        self.academic_year_natural_id = academic_year_natural_id
        super().__init__(
            f"no configuration draft exists for school={school_natural_id!r}, "
            f"academic_year={academic_year_natural_id!r}"
        )


class InitialDraftCannotBeDiscardedError(Exception):
    """Safe Configuration Changes, Slice B: `discard_draft` was called
    for a school/year that has never had a successful `Generate` --
    the year's one and only `ConfigurationRevision` is the initial,
    pre-first-Generate DRAFT, which is the year's only editable
    configuration and is required for that first `Generate` to ever
    succeed. Discarding it would leave the year with no configuration
    revision at all, violating Slice A's own invariant that every
    `AcademicYear` always has a draft or a published revision. Carries
    only natural IDs."""

    def __init__(self, school_natural_id: str, academic_year_natural_id: str) -> None:
        self.school_natural_id = school_natural_id
        self.academic_year_natural_id = academic_year_natural_id
        super().__init__(
            f"the initial pre-first-Generate configuration draft cannot be discarded for "
            f"school={school_natural_id!r}, academic_year={academic_year_natural_id!r}"
        )


class ScheduleOutOfDateError(Exception):
    """Safe Configuration Changes, Slice B, Owner Decision 1: the active
    `Schedule` is read-only while a configuration draft is open for this
    `AcademicYear` -- `Move`/`Lock`/`Unlock`/`Reoptimize`/`Restore` are
    all rejected until the draft is discarded or a future regeneration
    (Slice C) succeeds. Historical/active timetable GETs and Version
    History remain unaffected -- this error is only ever raised by a
    *mutating* schedule command. Carries only natural IDs, never a
    persistence surrogate ID."""

    def __init__(self, school_natural_id: str, academic_year_natural_id: str) -> None:
        self.school_natural_id = school_natural_id
        self.academic_year_natural_id = academic_year_natural_id
        super().__init__(
            f"the active schedule is out of date for school={school_natural_id!r}, "
            f"academic_year={academic_year_natural_id!r}: scheduling configuration is "
            "currently being edited; the existing timetable is read-only until the draft "
            "is discarded or a future regeneration succeeds"
        )


class IncompatibleLocksRequireConfirmationError(Exception):
    """Safe Configuration Changes, Slice C: one or more `LockedOccurrence`s
    belonging to the current active `ScheduleVersion` are incompatible
    with the currently open draft configuration (Owner Decision #39 item
    (4): "identify affected locks, show the admin, require explicit
    confirmation before continuing without them"). Regeneration is
    refused, with zero mutation and the solver never invoked, until the
    caller explicitly confirms the EXACT natural-ID set of incompatible
    locks it accepts dropping.

    A bare boolean confirmation is never accepted, by design: the caller
    must echo back precisely this error's own `incompatible_locks` keys
    (never a persistence surrogate ID) as a retried call's
    `confirmed_incompatible_lock_keys`. `ScheduleVersionRepository.
    persist_regenerated_version` recomputes this same classification
    again, authoritatively, under the `AcademicYear` row lock immediately
    before persisting -- if a configuration write changed lock
    compatibility in the race window since the caller last saw it, this
    same error is raised again with the FRESH classification rather than
    silently proceeding against a stale confirmation.

    Carries the full structured classification
    (`scheduling.lock_compatibility.IncompatibleLock`: each lock's own
    `OccurrenceKey` plus a stable, API-safe `reason_code` and a
    human-readable `message`) as an immutable tuple -- reused directly,
    never duplicated into a parallel `application/`-owned type, the same
    way `InvalidSchedulingConfigurationError` above already reuses
    `validation.errors.ValidationError` directly. `application/` already
    depends on `scheduling/` for this exact kind of decision
    (`GenerateScheduleService`/`ScheduleEditingService` already import
    `scheduling.solver`/`scheduling.reoptimize` directly), so this import
    introduces no new dependency direction."""

    def __init__(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        incompatible_locks: tuple[IncompatibleLock, ...],
    ) -> None:
        self.school_natural_id = school_natural_id
        self.academic_year_natural_id = academic_year_natural_id
        self.incompatible_locks = incompatible_locks
        super().__init__(
            f"regeneration for school={school_natural_id!r}, academic_year="
            f"{academic_year_natural_id!r} requires confirmation for "
            f"{len(incompatible_locks)} incompatible locked occurrence(s): "
            f"{[lock.reason_code for lock in incompatible_locks]!r}"
        )
