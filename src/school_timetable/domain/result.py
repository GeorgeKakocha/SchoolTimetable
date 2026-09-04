"""Solver output types."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class SolverStatus(str, Enum):
    OPTIMAL = "OPTIMAL"
    FEASIBLE = "FEASIBLE"
    INFEASIBLE = "INFEASIBLE"
    INVALID_INPUT = "INVALID_INPUT"
    ERROR = "ERROR"


class EntrySource(str, Enum):
    REQUIREMENT = "REQUIREMENT"
    RESERVED_BLOCK = "RESERVED_BLOCK"


@dataclass(frozen=True)
class ScheduleEntry:
    source: EntrySource
    activity_id: str
    day_id: str
    period_id: str
    class_sections: tuple[str, ...]
    teacher_id: str | None = None
    participant_group_id: str | None = None
    resource_id: str | None = None
    requirement_id: str | None = None
    reserved_block_id: str | None = None


@dataclass(frozen=True)
class SchedulingResult:
    status: SolverStatus
    entries: tuple[ScheduleEntry, ...] = ()
    total_soft_penalty: int = 0
    validation_errors: tuple = ()
    metadata: dict = field(default_factory=dict)

    @property
    def is_success(self) -> bool:
        return self.status in (SolverStatus.OPTIMAL, SolverStatus.FEASIBLE)
