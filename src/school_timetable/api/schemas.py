"""Explicit Pydantic response models for the read-only scheduling
configuration API (Phase 3A2.4) and, since Phase 3A3.4, the schedule
generation/read API (`docs/DECISIONS.md` #31's locked HTTP contract).

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

from pydantic import BaseModel


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
