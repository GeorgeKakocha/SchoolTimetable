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

from pydantic import BaseModel, Field


class SchoolResponse(BaseModel):
    id: str
    name: str


class AcademicYearResponse(BaseModel):
    id: str
    label: str


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


class ActiveScheduleResponse(BaseModel):
    """`GET .../schedule/active`'s success body: the same public
    version-summary fields as `GenerateScheduleResponse`, plus `entries`
    -- ordered exactly by the persisted `schedule_entry.ordinal`, which
    is itself never exposed."""

    version_number: int
    solver_status: Literal["OPTIMAL", "FEASIBLE"]
    total_soft_penalty: int
    created_at: datetime
    is_active: bool
    entries: tuple[ScheduleEntryResponse, ...]


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


class TeacherOptionResponse(BaseModel):
    id: str
    name: str


class ActivityOptionResponse(BaseModel):
    id: str
    name: str


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


class TeachingAssignmentWriteRequest(BaseModel):
    """POST/PUT request body -- maps 1:1 onto the locked
    `TeachingAssignmentFields` application dataclass (Decision #34).
    `participant_group_id` must be sourced verbatim from a prior GET's
    `whole_class_targets[*].participant_group_id`; the frontend never
    infers or constructs it. Pydantic enforces only basic request
    shape/type here -- `TeachingAssignmentService`/`teaching_assignment_rules`
    remain the sole authoritative validators, including when called
    outside HTTP."""

    teacher_id: str
    participant_group_id: str
    activity_id: str
    weekly_periods: int = Field(gt=0)


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
