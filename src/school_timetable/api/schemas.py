"""Explicit Pydantic response models for the read-only scheduling
configuration API (Phase 3A2.4), the schedule generation/read API
(Phase 3A3.4, `docs/DECISIONS.md` #31's locked HTTP contract), and the
Teaching Assignments HTTP API (Phase 3C.2b, `docs/DECISIONS.md`
#34-#36's locked HTTP contract).

Hand-designed, one field at a time, mirroring the current `domain/`
dataclasses exactly -- never a generic `dataclasses.asdict()`/reflection
dump, so a future domain field never leaks into the public API contract
by accident (see `docs/DECISIONS.md` #30). Every ID here is the
domain's own natural string ID; no persistence surrogate `BIGINT` and no
ORM `ordinal` value is ever exposed. Deliberately imports no
`domain/`/`persistence/` types itself -- pure wire-contract shapes; the
mapping from domain/application objects to these models lives in
`api/serializer.py`.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class SchoolResponse(BaseModel):
    id: str
    name: str


class AcademicYearResponse(BaseModel):
    id: str
    label: str


# -- School/initial-year provisioning. -------------------------------------

_PUBLIC_ID_PATTERN = r"^[a-z0-9][a-z0-9_-]*$"


class InitialAcademicYearProvisioningRequest(BaseModel):
    academic_year_id: str = Field(pattern=_PUBLIC_ID_PATTERN)
    label: str

    @field_validator("label")
    @classmethod
    def trim_nonblank_label(cls, value: str) -> str:
        value = value.strip()
        if value == "":
            raise ValueError("label must not be blank")
        return value


class ProvisionSchoolRequest(BaseModel):
    """Public IDs are immutable caller-chosen URL components: lowercase
    ASCII letters/digits plus hyphen/underscore, starting with a letter
    or digit. They are validated verbatim and never trimmed or rewritten;
    display values are trimmed at this boundary and again by the service."""

    school_id: str = Field(pattern=_PUBLIC_ID_PATTERN)
    school_name: str
    initial_academic_year: InitialAcademicYearProvisioningRequest

    @field_validator("school_name")
    @classmethod
    def trim_nonblank_name(cls, value: str) -> str:
        value = value.strip()
        if value == "":
            raise ValueError("school_name must not be blank")
        return value


class ProvisionSchoolResponse(BaseModel):
    school: SchoolResponse
    academic_year: AcademicYearResponse
    configuration_state: "ConfigurationRevisionStateResponse"


class SchoolProvisioningConflictErrorResponse(BaseModel):
    code: Literal["SCHOOL_ID_ALREADY_EXISTS"]
    detail: str


class AcademicYearProvisioningConflictErrorResponse(BaseModel):
    code: Literal["ACADEMIC_YEAR_ID_ALREADY_EXISTS"]
    detail: str


class InvalidSchoolProvisioningErrorResponse(BaseModel):
    code: Literal["INVALID_SCHOOL_PROVISIONING"]
    detail: str
    errors: tuple["ValidationDiagnosticResponse", ...]


class DayResponse(BaseModel):
    id: str
    name: str
    index: int


class PeriodResponse(BaseModel):
    id: str
    name: str
    index: int
    block_id: str
    is_instructional: bool
    start_time: str | None = None
    """Calendar A: `"HH:MM"`, or `null` when unset. Additive -- every
    pre-Calendar-A `/config` consumer already ignores unknown fields."""
    end_time: str | None = None


class ClassSectionResponse(BaseModel):
    id: str
    name: str


class ParticipantGroupResponse(BaseModel):
    id: str
    name: str
    class_sections: tuple[str, ...]
    role: str


class TeacherResponse(BaseModel):
    id: str
    name: str


class TeacherAvailabilityResponse(BaseModel):
    teacher_id: str
    day_id: str
    period_id: str
    status: str


class ActivityResponse(BaseModel):
    id: str
    name: str
    kind: str


class ResourceResponse(BaseModel):
    id: str
    name: str
    capacity: int


class LessonBlockPolicyResponse(BaseModel):
    mode: str
    block_sizes: tuple[int, ...]


class DistributionPolicyResponse(BaseModel):
    min_distinct_days: int | None
    max_periods_per_day: int | None


class TimePreferenceResponse(BaseModel):
    preferred_periods: tuple[int, ...]
    weight: str


class ResourceRequirementResponse(BaseModel):
    resource_id: str


class TeachingRequirementResponse(BaseModel):
    id: str
    teacher_id: str
    activity_id: str
    participant_group_id: str
    weekly_periods: int
    block_policy: LessonBlockPolicyResponse
    distribution_policy: DistributionPolicyResponse
    time_preferences: tuple[TimePreferenceResponse, ...]
    resource_requirement: ResourceRequirementResponse | None
    split_group_id: str | None


class TimeSlotResponse(BaseModel):
    day_id: str
    period_id: str


class ReservedBlockResponse(BaseModel):
    id: str
    name: str
    activity_id: str
    class_sections: tuple[str, ...]
    slots: tuple[TimeSlotResponse, ...]
    teacher_id: str | None
    resource_id: str | None = None


class FixedPlacementResponse(BaseModel):
    id: str
    requirement_id: str
    slot: TimeSlotResponse


class SchedulingConfigResponse(BaseModel):
    school: SchoolResponse
    academic_year: AcademicYearResponse
    days: tuple[DayResponse, ...]
    periods: tuple[PeriodResponse, ...]
    teachers: tuple[TeacherResponse, ...]
    class_sections: tuple[ClassSectionResponse, ...]
    participant_groups: tuple[ParticipantGroupResponse, ...]
    activities: tuple[ActivityResponse, ...]
    teaching_requirements: tuple[TeachingRequirementResponse, ...]
    resources: tuple[ResourceResponse, ...]
    teacher_availabilities: tuple[TeacherAvailabilityResponse, ...]
    reserved_blocks: tuple[ReservedBlockResponse, ...]
    fixed_placements: tuple[FixedPlacementResponse, ...]


# -- Schedule generation/read API (Phase 3A3.4, `docs/DECISIONS.md` #31's
# locked HTTP contract). ------------------------------------------------


class GenerateScheduleResponse(BaseModel):
    """`POST .../schedule/generate`'s success body -- exactly these five
    fields, locked by Decision #31: no entries, no `wall_time_seconds`,
    no `random_seed`, no surrogate ID, no `ordinal`, no CP-SAT
    telemetry."""

    version_number: int
    solver_status: Literal["OPTIMAL", "FEASIBLE"]
    total_soft_penalty: int
    created_at: datetime
    is_active: bool


class ScheduleEntryResponse(BaseModel):
    """One flat, locked-shape schedule entry -- exactly one of
    `requirement_id`/`reserved_block_id` populated, consistent with
    `source` (Decision #31)."""

    source: Literal["REQUIREMENT", "RESERVED_BLOCK"]
    day_id: str
    period_id: str
    requirement_id: str | None
    reserved_block_id: str | None
    activity_id: str
    teacher_id: str | None
    participant_group_id: str | None
    resource_id: str | None
    class_sections: tuple[str, ...]


class LockedOccurrenceResponse(BaseModel):
    """One locked logical occurrence (manual timetable editing backend
    slice) -- exactly `domain.schedule.OccurrenceKey`'s three natural-ID
    fields, never a persistence surrogate ID. A split-group lock always
    appears as one entry per sibling requirement (never collapsed),
    matching `lock_occurrence`'s own "lock every sibling together"
    domain semantics."""

    requirement_id: str
    day_id: str
    anchor_period_id: str


class ActiveScheduleResponse(BaseModel):
    """`GET .../schedule/active`'s success body: the same public
    version-summary fields as `GenerateScheduleResponse`, plus `entries`
    -- ordered exactly by the persisted `schedule_entry.ordinal`, which
    is itself never exposed -- and `locked_occurrences` (manual timetable
    editing backend slice; empty for a freshly generated version, since
    `persist_initial_version` never creates one). Every mutating editing
    command (`move`/`lock`/`unlock`/`reoptimize`) returns this exact same
    shape for its newly-active version, so a caller never needs a second
    GET to refresh the active timetable projection after a successful
    edit."""

    version_number: int
    solver_status: Literal["OPTIMAL", "FEASIBLE"]
    total_soft_penalty: int
    created_at: datetime
    is_active: bool
    entries: tuple[ScheduleEntryResponse, ...]
    locked_occurrences: tuple[LockedOccurrenceResponse, ...]


class ValidationDiagnosticResponse(BaseModel):
    """One safe preflight diagnostic -- mirrors `validation.errors.
    ValidationError` field-for-field; `context` is already natural-ID-only
    plain data, never a persistence object."""

    code: str
    message: str
    context: dict


class InvalidConfigurationResponse(BaseModel):
    """`POST .../schedule/generate`'s 422 body for
    `InvalidSchedulingConfigurationError` -- the stable `code` plus the
    validator's own diagnostics, in their original order."""

    code: Literal["INVALID_CONFIGURATION"]
    detail: str
    errors: tuple[ValidationDiagnosticResponse, ...]


class GenerationErrorResponse(BaseModel):
    """`POST .../schedule/generate`'s 409 body for
    `ScheduleAlreadyExistsError`/`ScheduleInfeasibleError` -- a stable
    `code` plus a safe, fixed detail string; never exception `repr`/`str`,
    never a persistence/natural ID."""

    code: str
    detail: str


# -- Manual timetable editing (ScheduleEditingService) -------------------


class MoveRequest(BaseModel):
    """`POST .../schedule/active/move`'s request body. The client never
    resolves split siblings or the swap target's own occupant itself --
    `scheduling.editing.validate_move`/`apply_move` do that -- it only
    ever names the one occurrence it wants moved and the one target slot
    it wants moved to."""

    base_version_number: int
    requirement_id: str
    source_day_id: str
    source_period_id: str
    target_day_id: str
    target_period_id: str


class MovePreviewRequest(BaseModel):
    """`POST .../schedule/active/move/preview`'s request body -- the
    exact same source-identification fields `MoveRequest` uses (never a
    second source-identification convention), minus the target: the
    whole point of this endpoint is to report every OTHER instructional
    slot's `validate_move` outcome for this one source."""

    base_version_number: int
    requirement_id: str
    source_day_id: str
    source_period_id: str


class MovePreviewTargetResponse(BaseModel):
    """One candidate destination's `validate_move` outcome. `violations`
    is empty whenever `allowed` is `True` -- never partially populated in
    either direction."""

    day_id: str
    period_id: str
    allowed: bool
    violations: tuple[MoveViolationResponse, ...]


class MovePreviewResponse(BaseModel):
    """`POST .../schedule/active/move/preview`'s success body. Never a
    write -- no `ScheduleVersion` is created, `version_number` is simply
    the currently active one this preview was computed against (the same
    value the caller must still pass back as `base_version_number` on
    the real move)."""

    version_number: int
    targets: tuple[MovePreviewTargetResponse, ...]


class LockRequest(BaseModel):
    """`POST .../schedule/active/lock`'s request body. Naming any one
    member of a split group locks every sibling together -- the domain
    layer decides the complete logical occurrence, never the client."""

    base_version_number: int
    requirement_id: str
    day_id: str
    period_id: str


class UnlockRequest(BaseModel):
    """`POST .../schedule/active/unlock`'s request body -- the exact
    same targeting contract as `LockRequest`."""

    base_version_number: int
    requirement_id: str
    day_id: str
    period_id: str


class ReoptimizeRequest(BaseModel):
    """`POST .../schedule/active/reoptimize`'s request body. No solver-
    options fields are exposed -- re-optimization always uses the
    server's own default `SolverOptions`, matching this slice's locked
    "do not invent a large solver-options API" scope."""

    base_version_number: int


class OccurrenceKeyRequest(BaseModel):
    """One natural-ID logical-occurrence key -- exactly `domain.schedule.
    OccurrenceKey`'s three fields, never a persistence surrogate ID.
    Structurally identical to `LockedOccurrenceResponse`, but kept as its
    own request-direction model (matching this codebase's existing
    request/response pairing convention, e.g. `LockRequest` vs.
    `LockedOccurrenceResponse`) rather than reusing a response schema as
    a request body."""

    requirement_id: str
    day_id: str
    anchor_period_id: str


class RegenerateScheduleRequest(BaseModel):
    """`POST .../schedule/active/regenerate`'s request body (Safe
    Configuration Changes, Slice C, Checkpoint 5). `base_version_number`
    is the same stale-version-protection field every other mutating
    editing command already requires. `confirmed_incompatible_lock_keys`
    defaults to empty -- valid exactly when the fresh regeneration attempt
    finds no incompatible locks; otherwise the caller must echo back
    precisely the natural-ID keys it was shown in a prior
    `IncompatibleLocksRequireConfirmationErrorResponse` (never a bare
    `confirm: true`) -- a stale or partial echo is rejected again with a
    fresh 409, never silently accepted."""

    base_version_number: int
    confirmed_incompatible_lock_keys: tuple[OccurrenceKeyRequest, ...] = ()


class IncompatibleLockResponse(BaseModel):
    """One incompatible locked occurrence from the FRESH classification
    carried by `IncompatibleLocksRequireConfirmationError` -- the
    natural-ID key plus `reason_code` (the stable, machine-readable
    contract a frontend confirmation UI branches on -- never requires
    parsing `message`) and `message` (human-readable explanatory text
    only). Never a database surrogate ID, never an ORM object."""

    requirement_id: str
    day_id: str
    anchor_period_id: str
    reason_code: str
    message: str


class IncompatibleLocksRequireConfirmationErrorResponse(BaseModel):
    """409 body for `IncompatibleLocksRequireConfirmationError` --
    `incompatible_locks` is the exact FRESH classification the caller
    must echo back verbatim, as `RegenerateScheduleRequest
    .confirmed_incompatible_lock_keys`, to proceed."""

    code: Literal["INCOMPATIBLE_LOCKS_REQUIRE_CONFIRMATION"]
    detail: str
    incompatible_locks: tuple[IncompatibleLockResponse, ...]


class StaleScheduleVersionErrorResponse(BaseModel):
    """409 body for `StaleScheduleVersionError` -- returned by every
    mutating editing command when the caller's `base_version_number` no
    longer matches the current active version."""

    code: Literal["STALE_SCHEDULE_VERSION"]
    detail: str
    expected_base_version_number: int
    actual_active_version_number: int


class MoveViolationResponse(BaseModel):
    code: str
    message: str


class MoveNotAllowedErrorResponse(BaseModel):
    """409 body for `MoveNotAllowedError` -- every `MoveViolation` the
    domain layer produced, in order, never flattened to a single generic
    message."""

    code: Literal["MOVE_NOT_ALLOWED"]
    detail: str
    violations: tuple[MoveViolationResponse, ...]


class InvalidEditTargetErrorResponse(BaseModel):
    """422 body for `InvalidEditTargetError` -- a move/lock/unlock
    request that named a (requirement, day, period) the domain layer
    could not even resolve into a valid logical occurrence."""

    code: Literal["INVALID_EDIT_TARGET"]
    detail: str


class ReoptimizationInfeasibleErrorResponse(BaseModel):
    code: Literal["REOPTIMIZATION_INFEASIBLE"]
    detail: str


class ReoptimizationInvalidInputErrorResponse(BaseModel):
    code: Literal["REOPTIMIZATION_INVALID_INPUT"]
    detail: str
    errors: tuple[ValidationDiagnosticResponse, ...]


# -- Schedule version history + restore API. `GET .../schedule/versions`
# lists metadata only (never entries/locks); the historical class/
# teacher projections reuse `ClassTimetableResponse`/
# `TeacherTimetableResponse` unchanged (their `is_active` field already
# supports a non-active historical version). `POST .../schedule/
# versions/{version_number}/restore` reuses `ActiveScheduleResponse`
# unchanged too -- a restore's success body is indistinguishable from
# any other mutating editing command's. -------------------------------


class ScheduleVersionSummaryResponse(BaseModel):
    """One row of the version history list -- metadata only."""

    version_number: int
    created_at: datetime
    solver_status: Literal["OPTIMAL", "FEASIBLE"]
    total_soft_penalty: int
    is_active: bool
    parent_version_number: int | None


class ScheduleVersionHistoryResponse(BaseModel):
    """`GET .../schedule/versions`'s success body -- newest
    `version_number` first, exactly one item with `is_active: true`."""

    versions: tuple[ScheduleVersionSummaryResponse, ...]


class RestoreVersionRequest(BaseModel):
    """`POST .../schedule/versions/{version_number}/restore`'s request
    body -- the URL path's `version_number` is the historical SOURCE to
    restore; `base_version_number` is the caller's believed-active
    version, the same stale-version-protection field every other
    mutating editing command already requires."""

    base_version_number: int


class ScheduleVersionNotFoundErrorResponse(BaseModel):
    """404 body for `ScheduleVersionNotFoundError` -- a requested
    historical `version_number` does not exist for this school/year's
    `Schedule`. Distinct from the code-less `{"detail": "Active schedule
    not found"}` body used when no `Schedule` exists at all."""

    code: Literal["SCHEDULE_VERSION_NOT_FOUND"]
    detail: str
    version_number: int


class VersionAlreadyActiveErrorResponse(BaseModel):
    """409 body for `VersionAlreadyActiveError` -- a restore request
    named the version that is already active."""

    code: Literal["VERSION_ALREADY_ACTIVE"]
    detail: str
    version_number: int


class RestoreVerificationFailedErrorResponse(BaseModel):
    """409 body for `RestoreVerificationFailedError` -- defense-in-depth
    only; expected never to occur in ordinary operation."""

    code: Literal["RESTORE_VERIFICATION_FAILED"]
    detail: str
    version_number: int


# -- Class-timetable projection API (Phase 3B.1, `docs/DECISIONS.md`
# #32). ------------------------------------------------------------


class DayHeaderResponse(BaseModel):
    id: str
    name: str


class ClassTimetableEntryResponse(BaseModel):
    """One flat, locked-shape projected lesson within a cell -- see
    `docs/DECISIONS.md` #32 Owner Decisions 3-4. A cell may carry more
    than one of these (parallel split-`ParticipantGroup` branches)."""

    source: Literal["REQUIREMENT", "RESERVED_BLOCK"]
    activity_id: str
    activity_name: str
    teacher_id: str | None
    teacher_name: str | None
    participant_group_id: str | None
    participant_group_name: str | None
    requirement_id: str | None
    reserved_block_id: str | None
    resource_id: str | None


class ClassTimetableCellResponse(BaseModel):
    day_id: str
    entries: tuple[ClassTimetableEntryResponse, ...]


class ClassTimetableRowResponse(BaseModel):
    period_id: str
    period_name: str
    cells: tuple[ClassTimetableCellResponse, ...]
    """Ordered to correspond exactly to `ClassTimetableResponse.days`."""


class ClassTimetableResponse(BaseModel):
    school_id: str
    school_name: str
    academic_year_id: str
    academic_year_label: str
    class_section_id: str
    class_section_name: str
    version_number: int
    solver_status: Literal["OPTIMAL", "FEASIBLE"]
    total_soft_penalty: int
    created_at: datetime
    is_active: bool
    days: tuple[DayHeaderResponse, ...]
    rows: tuple[ClassTimetableRowResponse, ...]


# -- Teacher-timetable projection API (next product slice after Phase
# 3C.3, no new phase number) -- sibling to the class-timetable
# projection above, same architecture, own independently-owned DTO
# family (never a reuse of the `ClassTimetable*Response` types, even
# where structurally similar, matching this module's existing
# convention -- see `TeachingAssignmentClassSectionResponse` below for
# the identical precedent). -----------------------------------------


class TeacherTimetableClassSectionResponse(BaseModel):
    id: str
    name: str


class TeacherTimetableEntryResponse(BaseModel):
    """One flat, locked-shape projected lesson within a cell. A hard
    solver constraint (teacher non-overlap) means a valid generated
    schedule has at most one of these per cell, but the shape stays
    structurally zero-or-more, matching
    `ClassTimetableCellResponse.entries` exactly, rather than assuming
    the invariant can never be violated by corrupt data."""

    source: Literal["REQUIREMENT", "RESERVED_BLOCK"]
    activity_id: str
    activity_name: str
    participant_group_id: str | None
    participant_group_name: str | None
    participant_group_role: str | None
    class_sections: tuple[TeacherTimetableClassSectionResponse, ...]
    requirement_id: str | None
    reserved_block_id: str | None
    resource_id: str | None


class TeacherTimetableCellResponse(BaseModel):
    day_id: str
    entries: tuple[TeacherTimetableEntryResponse, ...]


class TeacherTimetableRowResponse(BaseModel):
    period_id: str
    period_name: str
    cells: tuple[TeacherTimetableCellResponse, ...]
    """Ordered to correspond exactly to `TeacherTimetableResponse.days`."""


class TeacherTimetableResponse(BaseModel):
    school_id: str
    school_name: str
    academic_year_id: str
    academic_year_label: str
    teacher_id: str
    teacher_name: str
    version_number: int
    solver_status: Literal["OPTIMAL", "FEASIBLE"]
    total_soft_penalty: int
    created_at: datetime
    is_active: bool
    days: tuple[DayHeaderResponse, ...]
    rows: tuple[TeacherTimetableRowResponse, ...]


# -- Whole-school Teacher Matrix projection -------------------------------


class TeacherMatrixPeriodResponse(BaseModel):
    id: str
    name: str


class TeacherMatrixCellResponse(BaseModel):
    """One occupied coordinate. Absence of a coordinate means free."""

    day_id: str
    period_id: str
    entries: tuple[TeacherTimetableEntryResponse, ...]


class TeacherMatrixTeacherResponse(BaseModel):
    id: str
    name: str
    cells: tuple[TeacherMatrixCellResponse, ...]


class TeacherTimetableMatrixResponse(BaseModel):
    school_id: str
    school_name: str
    academic_year_id: str
    academic_year_label: str
    version_number: int
    solver_status: Literal["OPTIMAL", "FEASIBLE"]
    total_soft_penalty: int
    created_at: datetime
    is_active: bool
    days: tuple[DayHeaderResponse, ...]
    periods: tuple[TeacherMatrixPeriodResponse, ...]
    teachers: tuple[TeacherMatrixTeacherResponse, ...]


# -- Teaching Assignments API (Phase 3C.2b, `docs/DECISIONS.md` #34-#36's
# locked HTTP contract). --------------------------------------------------


class TeachingAssignmentClassSectionResponse(BaseModel):
    id: str
    name: str


class TeachingAssignmentResponse(BaseModel):
    """One `TeachingRequirement`, projected for display -- ALL
    requirements appear here, plain and advanced alike; `editable`/
    `advanced_reasons` are backend-owned (Decision #34's one locked
    plain-editable predicate), never inferred by a caller."""

    id: str
    teacher_id: str
    teacher_name: str
    activity_id: str
    activity_name: str
    participant_group_id: str
    participant_group_name: str
    participant_group_role: str
    class_sections: tuple[TeachingAssignmentClassSectionResponse, ...]
    weekly_periods: int
    editable: bool
    advanced_reasons: tuple[str, ...]
    resource_id: str | None


class TeacherOptionResponse(BaseModel):
    id: str
    name: str


class ActivityOptionResponse(BaseModel):
    id: str
    name: str


class TeachingAssignmentResourceOptionResponse(BaseModel):
    """The Resource catalog option list this page's "fixed Resource"
    select needs (Resources B1) -- kept local to this section, mirroring
    `TeacherOptionResponse`/`ActivityOptionResponse` immediately above,
    rather than reused from `ResourceProjectionItemResponse` defined
    later in this file (this module's classes are read top-to-bottom;
    every field this page needs already exists here, so no cross-section
    dependency is introduced)."""

    id: str
    name: str
    capacity: int


class WholeClassTargetResponse(BaseModel):
    """The authoritative, backend-owned mapping from a visible
    `ClassSection` to its canonical `WHOLE_CLASS` `ParticipantGroup` --
    the frontend must submit `participant_group_id` verbatim from here,
    never infer or construct it."""

    class_section_id: str
    class_section_name: str
    participant_group_id: str
    participant_group_name: str


class TeacherWorkloadResponse(BaseModel):
    """`total_weekly_periods` sums EVERY `TeachingRequirement` assigned
    to this teacher -- plain and advanced alike, never only the
    editable subset. A teacher with zero requirements still appears, at
    `0`."""

    teacher_id: str
    teacher_name: str
    total_weekly_periods: int


class TeachingAssignmentsProjectionResponse(BaseModel):
    """`GET .../teaching-assignments`'s success body -- the sole page
    projection the future Teaching Assignments admin page needs; it
    must never reconstruct business semantics from `/config` itself."""

    configuration_locked: bool
    assignments: tuple[TeachingAssignmentResponse, ...]
    teachers: tuple[TeacherOptionResponse, ...]
    whole_class_targets: tuple[WholeClassTargetResponse, ...]
    activities: tuple[ActivityOptionResponse, ...]
    teacher_workloads: tuple[TeacherWorkloadResponse, ...]
    resources: tuple[TeachingAssignmentResourceOptionResponse, ...]


class TeachingAssignmentWriteRequest(BaseModel):
    """POST/PUT request body -- maps 1:1 onto the locked
    `TeachingAssignmentFields` application dataclass (Decision #34,
    extended by Resources B1's locked Option A). `participant_group_id`
    must be sourced verbatim from a prior GET's
    `whole_class_targets[*].participant_group_id`; the frontend never
    infers or constructs it. Pydantic enforces only basic request
    shape/type here -- `TeachingAssignmentService`/`teaching_assignment_rules`
    remain the sole authoritative validators, including when called
    outside HTTP.

    `resource_id` is full-replacement, never PATCH: omitted (POST or
    PUT) or explicit `null` both mean "no fixed Resource" -- on PUT this
    always clears any Resource currently assigned, exactly like every
    other field here. A non-null value replaces/assigns that exact
    Resource."""

    teacher_id: str
    participant_group_id: str
    activity_id: str
    weekly_periods: int = Field(gt=0)
    resource_id: str | None = None


class TeachingAssignmentWriteResponse(BaseModel):
    """POST/PUT success body -- the caller must simply re-fetch
    `GET .../teaching-assignments` afterward for the refreshed page
    projection (workload totals, ordering, and lock state may all have
    changed); this response exists only to hand back the natural ID and
    any non-blocking save-time warnings (Decision #34's save-time
    validation boundary)."""

    id: str
    warnings: tuple[ValidationDiagnosticResponse, ...]


class TeachingAssignmentDeleteResponse(BaseModel):
    """DELETE success body -- 200, never 204, since a delete can
    legitimately surface non-blocking warnings (e.g. a newly-introduced
    `CLASS_OCCUPANCY_MISMATCH`) that a bodyless response would silently
    discard."""

    deleted_id: str
    warnings: tuple[ValidationDiagnosticResponse, ...]


class UnknownReferenceErrorResponse(BaseModel):
    code: Literal["UNKNOWN_REFERENCE"]
    detail: str
    reference_kind: str
    reference_id: str


class NonWholeClassTargetErrorResponse(BaseModel):
    code: Literal["NON_WHOLE_CLASS_TARGET"]
    detail: str
    participant_group_id: str
    actual_role: str


class NonOrdinaryActivityTargetErrorResponse(BaseModel):
    code: Literal["NON_ORDINARY_ACTIVITY_TARGET"]
    detail: str
    activity_id: str
    actual_kind: str


class AdvancedRequirementNotEditableErrorResponse(BaseModel):
    """Exposes `advanced_reasons` even though a prior GET already would
    have -- this protects a stale client whose displayed disabled-state
    no longer matches the server's authoritative recheck."""

    code: Literal["ADVANCED_REQUIREMENT_NOT_EDITABLE"]
    detail: str
    advanced_reasons: tuple[str, ...]


class DuplicateTeachingAssignmentErrorResponse(BaseModel):
    code: Literal["DUPLICATE_TEACHING_ASSIGNMENT"]
    detail: str
    teacher_id: str
    participant_group_id: str
    activity_id: str


class InvalidTeachingAssignmentErrorResponse(BaseModel):
    code: Literal["INVALID_TEACHING_ASSIGNMENT"]
    detail: str
    errors: tuple[ValidationDiagnosticResponse, ...]


class ConfigurationLockedErrorResponse(BaseModel):
    """The stable Decision #35 HTTP contract."""

    code: Literal["SCHEDULING_CONFIGURATION_LOCKED"]
    detail: str


# -- Atomic synchronized two-branch split API. ---------------------------


class SynchronizedSplitBranchRequest(BaseModel):
    participant_group_name: str
    teacher_id: str
    activity_id: str


class SynchronizedSplitCreateRequest(BaseModel):
    class_section_id: str
    weekly_periods: int = Field(gt=0)
    branches: tuple[SynchronizedSplitBranchRequest, SynchronizedSplitBranchRequest]


class SynchronizedSplitBranchResponse(BaseModel):
    participant_group_id: str
    participant_group_name: str
    teacher_id: str
    activity_id: str
    requirement_id: str


class SynchronizedSplitCreateResponse(BaseModel):
    split_group_id: str
    class_section_id: str
    weekly_periods: int
    branches: tuple[SynchronizedSplitBranchResponse, SynchronizedSplitBranchResponse]
    warnings: tuple[ValidationDiagnosticResponse, ...]


class DuplicateSubgroupNamesErrorResponse(BaseModel):
    code: Literal["DUPLICATE_SUBGROUP_NAMES"]
    detail: str


class SameTeacherSynchronizedSplitErrorResponse(BaseModel):
    code: Literal["SAME_TEACHER_SYNCHRONIZED_SPLIT"]
    detail: str
    teacher_id: str


class InvalidSynchronizedSplitErrorResponse(BaseModel):
    code: Literal["INVALID_SYNCHRONIZED_SPLIT"]
    detail: str
    errors: tuple[ValidationDiagnosticResponse, ...]


class SynchronizedSplitPublicIdCollisionErrorResponse(BaseModel):
    code: Literal["SYNCHRONIZED_SPLIT_PUBLIC_ID_COLLISION"]
    detail: str


# -- Teacher CRUD API (Real-School Setup MVP Slice B). -------------------


class TeacherProjectionItemResponse(BaseModel):
    """`name` is `Teacher.full_name` -- included so every consumer (the
    future School Setup UI included) never has to re-implement the
    trim/join rule itself; `first_name`/`last_name` remain the raw
    authoritative edit fields."""

    id: str
    first_name: str
    last_name: str
    name: str


class TeachersProjectionResponse(BaseModel):
    configuration_locked: bool
    teachers: tuple[TeacherProjectionItemResponse, ...]


class TeacherWriteRequest(BaseModel):
    """POST/PUT request body -- maps 1:1 onto `TeacherFields` (Owner
    Decision #37). The natural ID is never accepted here -- it is
    always server-generated on create and immutable on update (path
    parameter only)."""

    first_name: str
    last_name: str


class TeacherWriteResponse(BaseModel):
    """POST/PUT success body -- the full written `Teacher`'s own
    resolved fields. Unlike `TeachingAssignmentWriteResponse`, a Teacher
    write affects nothing but its own row, so there is no larger page
    projection to protect against staleness by withholding fields."""

    id: str
    first_name: str
    last_name: str
    name: str


class TeacherDeleteResponse(BaseModel):
    """DELETE success body. No `warnings` field -- Teacher writes have
    no TeachingAssignment-style quantitative warning mechanism."""

    deleted_id: str


class InvalidTeacherErrorResponse(BaseModel):
    code: Literal["INVALID_TEACHER"]
    detail: str
    errors: tuple[ValidationDiagnosticResponse, ...]


class TeacherInUseErrorResponse(BaseModel):
    code: Literal["TEACHER_IN_USE"]
    detail: str
    referenced_by: tuple[str, ...]


# -- Class CRUD API (Real-School Setup MVP Slice C). ----------------------


class ClassSectionProjectionItemResponse(BaseModel):
    """The canonical WHOLE_CLASS group's own id/role/membership are
    deliberately never exposed here -- it remains an internal
    scheduling implementation detail (Owner Decision #33)."""

    id: str
    name: str


class ClassSectionsProjectionResponse(BaseModel):
    configuration_locked: bool
    classes: tuple[ClassSectionProjectionItemResponse, ...]


class ClassSectionWriteRequest(BaseModel):
    """POST/PUT request body -- maps 1:1 onto `ClassSectionFields`. The
    natural ID (and the internal canonical group) is never accepted
    here -- always server-generated on create, immutable on update
    (path parameter only)."""

    name: str


class ClassSectionWriteResponse(BaseModel):
    """POST/PUT success body -- the written `ClassSection`'s own
    resolved fields. No canonical-group ID -- internal only."""

    id: str
    name: str


class ClassSectionDeleteResponse(BaseModel):
    """DELETE success body. No `warnings` field, no canonical-group ID."""

    deleted_id: str


class InvalidClassErrorResponse(BaseModel):
    code: Literal["INVALID_CLASS"]
    detail: str
    errors: tuple[ValidationDiagnosticResponse, ...]


class DuplicateClassErrorResponse(BaseModel):
    code: Literal["DUPLICATE_CLASS"]
    detail: str


class ClassSectionInUseErrorResponse(BaseModel):
    code: Literal["CLASS_IN_USE"]
    detail: str
    referenced_by: tuple[str, ...]


# -- Subject CRUD API (Real-School Setup MVP Slice D). ---------------------


class SubjectProjectionItemResponse(BaseModel):
    """No `kind` field -- every member of this resource is already,
    by construction, `ActivityKind.ORDINARY` (Owner Decision -- Subject
    = Activity(kind=ORDINARY), never a separate domain entity)."""

    id: str
    name: str


class SubjectsProjectionResponse(BaseModel):
    configuration_locked: bool
    subjects: tuple[SubjectProjectionItemResponse, ...]


class SubjectWriteRequest(BaseModel):
    """POST/PUT request body -- maps 1:1 onto `SubjectFields`. The
    natural ID and `kind` are never accepted here -- the natural ID is
    always server-generated on create and immutable on update (path
    parameter only), and `kind` is always `ORDINARY`, never
    client-controlled."""

    name: str


class SubjectWriteResponse(BaseModel):
    """POST/PUT success body -- the written `Activity`'s own resolved
    fields. No `kind` -- internal/always ORDINARY on this surface."""

    id: str
    name: str


class SubjectDeleteResponse(BaseModel):
    """DELETE success body. No `warnings` field, no `kind`."""

    deleted_id: str


class InvalidSubjectErrorResponse(BaseModel):
    code: Literal["INVALID_SUBJECT"]
    detail: str
    errors: tuple[ValidationDiagnosticResponse, ...]


class DuplicateSubjectErrorResponse(BaseModel):
    code: Literal["DUPLICATE_SUBJECT"]
    detail: str


class SubjectInUseErrorResponse(BaseModel):
    code: Literal["SUBJECT_IN_USE"]
    detail: str
    referenced_by: tuple[str, ...]


# -- Special Activity CRUD API (Reserved Activities Slice A1). -------------


class SpecialActivityProjectionItemResponse(BaseModel):
    """No `kind` field -- every member of this resource is already,
    by construction, `ActivityKind.CLUB` (Special Activity =
    Activity(kind=CLUB), never a separate domain entity)."""

    id: str
    name: str


class SpecialActivitiesProjectionResponse(BaseModel):
    configuration_locked: bool
    special_activities: tuple[SpecialActivityProjectionItemResponse, ...]


class SpecialActivityWriteRequest(BaseModel):
    """POST/PUT request body -- maps 1:1 onto `SpecialActivityFields`.
    The natural ID and `kind` are never accepted here -- the natural ID
    is always server-generated on create and immutable on update (path
    parameter only), and `kind` is always `CLUB`, never
    client-controlled."""

    name: str


class SpecialActivityWriteResponse(BaseModel):
    """POST/PUT success body -- the written `Activity`'s own resolved
    fields. No `kind` -- internal/always CLUB on this surface."""

    id: str
    name: str


class SpecialActivityDeleteResponse(BaseModel):
    """DELETE success body. No `warnings` field, no `kind`."""

    deleted_id: str


class InvalidSpecialActivityErrorResponse(BaseModel):
    code: Literal["INVALID_SPECIAL_ACTIVITY"]
    detail: str
    errors: tuple[ValidationDiagnosticResponse, ...]


class DuplicateSpecialActivityErrorResponse(BaseModel):
    code: Literal["DUPLICATE_SPECIAL_ACTIVITY"]
    detail: str


class SpecialActivityInUseErrorResponse(BaseModel):
    code: Literal["SPECIAL_ACTIVITY_IN_USE"]
    detail: str
    referenced_by: tuple[str, ...]


# -- Reserved Activity CRUD API (Reserved Activities Slice A2). ------------


class ReservedActivitySpecialActivityOptionResponse(BaseModel):
    id: str
    name: str


class ReservedActivityTeacherOptionResponse(BaseModel):
    id: str
    name: str


class ReservedActivityClassSectionOptionResponse(BaseModel):
    id: str
    name: str


class ReservedActivityDayOptionResponse(BaseModel):
    id: str
    name: str
    index: int


class ReservedActivityPeriodOptionResponse(BaseModel):
    id: str
    name: str
    index: int
    is_instructional: bool


class ReservedActivitySlotResponse(BaseModel):
    day_id: str
    period_id: str


class ReservedActivityResourceOptionResponse(BaseModel):
    """The Resource catalog option list this page's "fixed Resource"
    select needs (Resources B2) -- kept local to this section, mirroring
    `ReservedActivityTeacherOptionResponse` immediately above rather
    than reused from `ResourceProjectionItemResponse` defined later in
    this file."""

    id: str
    name: str
    capacity: int


class ReservedActivityItemResponse(BaseModel):
    """No `name`/`special_activity_name`/`teacher_name`/class-section
    names -- every reference here is a bare natural ID, resolved by
    the consumer against this same response's own top-level catalogs."""

    id: str
    special_activity_id: str
    class_section_ids: tuple[str, ...]
    teacher_id: str | None
    slots: tuple[ReservedActivitySlotResponse, ...]
    resource_id: str | None = None


class ReservedActivitiesProjectionResponse(BaseModel):
    configuration_locked: bool
    special_activities: tuple[ReservedActivitySpecialActivityOptionResponse, ...]
    teachers: tuple[ReservedActivityTeacherOptionResponse, ...]
    class_sections: tuple[ReservedActivityClassSectionOptionResponse, ...]
    days: tuple[ReservedActivityDayOptionResponse, ...]
    periods: tuple[ReservedActivityPeriodOptionResponse, ...]
    reserved_activities: tuple[ReservedActivityItemResponse, ...]
    resources: tuple[ReservedActivityResourceOptionResponse, ...] = ()


class ReservedActivityWriteRequest(BaseModel):
    """POST/PUT request body -- the COMPLETE desired aggregate, never a
    partial patch. `teacher_id` is required but nullable -- an omitted
    `teacher_id` field is a normal request-validation failure, never
    silently defaulted. Never accepts `id`/`name`/`kind`/`ordinal`/
    `participant_group`/`duration`/`recurrence`.

    `resource_id` (Resources B2) is full-replacement, never PATCH:
    omitted or explicit `null` both mean "no fixed Resource" -- on PUT
    this always clears any Resource currently assigned, exactly like
    every other field here. A non-null value assigns/replaces that
    exact Resource."""

    special_activity_id: str
    class_section_ids: tuple[str, ...]
    teacher_id: str | None
    slots: tuple[ReservedActivitySlotResponse, ...]
    resource_id: str | None = None


class ReservedActivityWriteResponse(BaseModel):
    """POST/PUT success body -- the written `ReservedBlock`'s own
    resolved fields, already in canonical persisted order. No `name`
    -- server-derived, internal, never exposed on this surface."""

    id: str
    special_activity_id: str
    class_section_ids: tuple[str, ...]
    teacher_id: str | None
    slots: tuple[ReservedActivitySlotResponse, ...]
    resource_id: str | None = None


class ReservedActivityDeleteResponse(BaseModel):
    deleted_id: str


class InvalidReservedActivityErrorResponse(BaseModel):
    code: Literal["INVALID_RESERVED_ACTIVITY"]
    detail: str
    errors: tuple[ValidationDiagnosticResponse, ...]


class NonSpecialActivityTargetErrorResponse(BaseModel):
    """Deliberately carries only `activity_id` -- never `actual_kind`/
    `kind`, and never the raw `CLUB`/`ORDINARY` vocabulary, unlike
    Teaching Assignments' unrelated `NON_ORDINARY_ACTIVITY_TARGET`
    contract."""

    code: Literal["NON_SPECIAL_ACTIVITY_TARGET"]
    detail: str
    activity_id: str


# -- Teacher Availability API (Owner Decision #38). ------------------------


class TeacherAvailabilityTeacherResponse(BaseModel):
    id: str
    name: str


class TeacherAvailabilityDayResponse(BaseModel):
    id: str
    name: str
    index: int


class TeacherAvailabilityPeriodResponse(BaseModel):
    id: str
    name: str
    index: int
    block_id: str
    is_instructional: bool


class TeacherAvailabilityExceptionResponse(BaseModel):
    """One sparse exception cell -- `status` is always `PREFER_NOT` or
    `UNAVAILABLE`; `AVAILABLE` is never a member of this response
    (Owner Decision #38's sparse contract -- represented by a cell's
    absence, never an explicit row)."""

    teacher_id: str
    day_id: str
    period_id: str
    status: str


class TeacherAvailabilityProjectionResponse(BaseModel):
    configuration_locked: bool
    teachers: tuple[TeacherAvailabilityTeacherResponse, ...]
    days: tuple[TeacherAvailabilityDayResponse, ...]
    periods: tuple[TeacherAvailabilityPeriodResponse, ...]
    exceptions: tuple[TeacherAvailabilityExceptionResponse, ...]


class TeacherAvailabilityExceptionRequest(BaseModel):
    """One requested exception cell. `status` is deliberately a plain
    `str`, never a Pydantic `Literal` -- every status value (including
    `AVAILABLE` and any unrecognized string) must reach the
    application-level `teacher_availability_rules.validate_replace`
    validation, so every kind of bad status produces the exact same
    `INVALID_TEACHER_AVAILABILITY` contract rather than FastAPI's
    generic, differently-shaped Pydantic validation-error body."""

    day_id: str
    period_id: str
    status: str


class TeacherAvailabilityReplaceRequest(BaseModel):
    """PUT request body -- the complete desired sparse exception set
    for one Teacher. An empty `exceptions` list clears every explicit
    exception for that Teacher."""

    exceptions: tuple[TeacherAvailabilityExceptionRequest, ...]


class TeacherAvailabilityWriteExceptionResponse(BaseModel):
    """One saved exception cell within a PUT response -- no `teacher_id`
    (already the write response's own top-level field, so never
    repeated per cell, unlike the flat multi-teacher projection's
    `TeacherAvailabilityExceptionResponse`)."""

    day_id: str
    period_id: str
    status: str


class TeacherAvailabilityWriteResponse(BaseModel):
    """PUT success body -- the written Teacher's own resolved,
    authoritative exception set. Never the full page projection --
    matches `TeacherWriteResponse`'s "hand back the written row's own
    state" discipline."""

    teacher_id: str
    exceptions: tuple[TeacherAvailabilityWriteExceptionResponse, ...]


class InvalidTeacherAvailabilityErrorResponse(BaseModel):
    code: Literal["INVALID_TEACHER_AVAILABILITY"]
    detail: str
    errors: tuple[ValidationDiagnosticResponse, ...]


# -- Resource Catalog API (Resources Slice A). ------------------------------


class ResourceProjectionItemResponse(BaseModel):
    """`Resource` is the existing `domain.resources.Resource(id, name,
    capacity)` -- not a new/redesigned entity. `capacity` means
    "maximum number of simultaneous lesson/resource occupations,"
    never student-seat/room-headcount capacity."""

    id: str
    name: str
    capacity: int


class ResourcesProjectionResponse(BaseModel):
    configuration_locked: bool
    resources: tuple[ResourceProjectionItemResponse, ...]


class ResourceWriteRequest(BaseModel):
    """POST/PUT request body -- maps 1:1 onto `ResourceFields`. The
    natural ID is never accepted here -- always server-generated on
    create and immutable on update (path parameter only)."""

    name: str
    capacity: int


class ResourceWriteResponse(BaseModel):
    """POST/PUT success body -- the written `Resource`'s own resolved
    fields."""

    id: str
    name: str
    capacity: int


class ResourceDeleteResponse(BaseModel):
    """DELETE success body."""

    deleted_id: str


class InvalidResourceErrorResponse(BaseModel):
    code: Literal["INVALID_RESOURCE"]
    detail: str
    errors: tuple[ValidationDiagnosticResponse, ...]


class DuplicateResourceErrorResponse(BaseModel):
    code: Literal["DUPLICATE_RESOURCE"]
    detail: str


class ResourceInUseErrorResponse(BaseModel):
    code: Literal["RESOURCE_IN_USE"]
    detail: str
    referenced_by: tuple[str, ...]


# -- Calendar A (`GET/POST/PUT/DELETE .../calendar/...`). ------------------
# `start_time`/`end_time` are always `"HH:MM"` strings (never a raw
# `datetime.time`/ISO value with seconds) -- a plain `str | None` field
# is the simplest wire shape needing zero custom Pydantic (de)serializer
# plumbing; parsing/formatting lives in `api/serializer.py`. Raw
# `block_id` is never part of this public contract -- `starts_new_block`
# is the only block-boundary field a client ever sees or sends.


class CalendarDayResponse(BaseModel):
    id: str
    name: str
    index: int


class CalendarPeriodResponse(BaseModel):
    id: str
    name: str
    index: int
    start_time: str | None
    end_time: str | None
    starts_new_block: bool
    is_instructional: bool


class CalendarProjectionResponse(BaseModel):
    configuration_locked: bool
    days: tuple[CalendarDayResponse, ...]
    periods: tuple[CalendarPeriodResponse, ...]


class DayWriteRequest(BaseModel):
    """POST/PUT request body. The natural ID is never accepted here --
    always server-generated on create and immutable on update. Raw
    numeric `index` is never accepted here either -- ordering changes
    only via `POST .../days/{day_id}/move`."""

    name: str


class DayWriteResponse(BaseModel):
    """POST/PUT success body -- the written Day's own resolved fields."""

    id: str
    name: str
    index: int


class DayDeleteResponse(BaseModel):
    """DELETE success body."""

    deleted_id: str


class DayMoveRequest(BaseModel):
    direction: Literal["up", "down"]


class PeriodWriteRequest(BaseModel):
    """POST/PUT request body. The natural ID is never accepted here.
    `is_instructional` is deliberately absent (locked policy) -- new
    Periods are always instructional, and an existing legacy
    `is_instructional=False` row's own flag is preserved unchanged
    regardless of this request body."""

    name: str
    start_time: str | None = None
    end_time: str | None = None
    starts_new_block: bool = False


class PeriodWriteResponse(BaseModel):
    """POST/PUT success body -- the written Period's own resolved
    fields."""

    id: str
    name: str
    index: int
    start_time: str | None
    end_time: str | None
    starts_new_block: bool
    is_instructional: bool


class PeriodDeleteResponse(BaseModel):
    """DELETE success body."""

    deleted_id: str


class PeriodMoveRequest(BaseModel):
    direction: Literal["up", "down"]


class InvalidDayErrorResponse(BaseModel):
    code: Literal["INVALID_DAY"]
    detail: str
    errors: tuple[ValidationDiagnosticResponse, ...]


class DuplicateDayErrorResponse(BaseModel):
    code: Literal["DUPLICATE_DAY"]
    detail: str


class DayInUseErrorResponse(BaseModel):
    code: Literal["DAY_IN_USE"]
    detail: str
    referenced_by: tuple[str, ...]


class InvalidPeriodErrorResponse(BaseModel):
    code: Literal["INVALID_PERIOD"]
    detail: str
    errors: tuple[ValidationDiagnosticResponse, ...]


class DuplicatePeriodErrorResponse(BaseModel):
    code: Literal["DUPLICATE_PERIOD"]
    detail: str


class PeriodInUseErrorResponse(BaseModel):
    code: Literal["PERIOD_IN_USE"]
    detail: str
    referenced_by: tuple[str, ...]


class PeriodReorderBlockedErrorResponse(BaseModel):
    code: Literal["PERIOD_REORDER_BLOCKED_BY_TIME_PREFERENCES"]
    detail: str


# -- Safe Configuration Changes, Slice B: draft configuration lifecycle. ----


class ConfigurationRevisionStateResponse(BaseModel):
    """`GET .../configuration/state`'s success body -- also returned by
    a successful `POST .../configuration/draft`. Never a persistence
    surrogate ID; `published_revision_number`/`draft_revision_number`
    are natural `ConfigurationRevision.revision_number` values, `None`
    exactly when no revision of that status exists for this year."""

    published_revision_number: int | None
    draft_revision_number: int | None
    configuration_locked: bool
    timetable_out_of_date: bool


class NoConfigurationDraftErrorResponse(BaseModel):
    """409 body for `NoConfigurationDraftError` -- `DELETE
    .../configuration/draft` was called with no open draft."""

    code: Literal["NO_CONFIGURATION_DRAFT"]
    detail: str


class InitialDraftCannotBeDiscardedErrorResponse(BaseModel):
    """409 body for `InitialDraftCannotBeDiscardedError` -- the year's
    only revision is its initial pre-first-Generate draft, required for
    that first Generate to ever succeed."""

    code: Literal["INITIAL_DRAFT_CANNOT_BE_DISCARDED"]
    detail: str


class ScheduleOutOfDateErrorResponse(BaseModel):
    """409 body for `ScheduleOutOfDateError` -- Owner Decision 1: a
    mutating schedule command (Move/Lock/Unlock/Reoptimize/Restore) was
    rejected because a configuration draft is currently open."""

    code: Literal["SCHEDULE_OUT_OF_DATE"]
    detail: str
