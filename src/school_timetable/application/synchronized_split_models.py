"""Application-owned contracts for an atomic synchronized two-branch split."""
from __future__ import annotations

from dataclasses import dataclass

from school_timetable.validation.errors import ValidationError


@dataclass(frozen=True)
class SynchronizedSplitBranchFields:
    participant_group_name: str
    teacher_id: str
    activity_id: str


@dataclass(frozen=True)
class CreateSynchronizedSplitCommand:
    school_id: str
    academic_year_id: str
    class_section_id: str
    weekly_periods: int
    branch_a: SynchronizedSplitBranchFields
    branch_b: SynchronizedSplitBranchFields


@dataclass(frozen=True)
class SynchronizedSplitPublicIds:
    split_group_id: str
    branch_a_group_id: str
    branch_b_group_id: str
    branch_a_requirement_id: str
    branch_b_requirement_id: str


@dataclass(frozen=True)
class SynchronizedSplitBranchResult:
    participant_group_id: str
    participant_group_name: str
    teacher_id: str
    activity_id: str
    requirement_id: str


@dataclass(frozen=True)
class SynchronizedSplitResult:
    split_group_id: str
    class_section_id: str
    weekly_periods: int
    branch_a: SynchronizedSplitBranchResult
    branch_b: SynchronizedSplitBranchResult
    warnings: tuple[ValidationError, ...] = ()
