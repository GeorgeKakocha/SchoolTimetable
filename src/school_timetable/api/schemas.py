"""Explicit Pydantic response models for the read-only scheduling
configuration API (Phase 3A2.4).

Hand-designed, one field at a time, mirroring the current `domain/`
dataclasses exactly -- never a generic `dataclasses.asdict()`/reflection
dump, so a future domain field never leaks into the public API contract
by accident (see `docs/DECISIONS.md` #30). Every ID here is the
domain's own natural string ID; no persistence surrogate `BIGINT` and no
ORM `ordinal` value is ever exposed. Deliberately imports no
`domain/`/`persistence/` types itself -- pure wire-contract shapes; the
mapping from domain objects to these models lives in `api/serializer.py`.
"""
from __future__ import annotations

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
