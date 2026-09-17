"""Application-owned read model for a whole-school Teacher Matrix.

The dimensions are ordered, while each teacher carries only occupied cells.
An absent ``(day_id, period_id)`` coordinate is therefore explicitly a free
period.  Every identifier is a public/domain natural ID; persistence
surrogate IDs and ordinals are not part of this contract.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from school_timetable.application.teacher_timetable_models import TeacherTimetableEntry
from school_timetable.domain.result import SolverStatus


@dataclass(frozen=True)
class TeacherMatrixDay:
    id: str
    name: str


@dataclass(frozen=True)
class TeacherMatrixPeriod:
    id: str
    name: str


@dataclass(frozen=True)
class TeacherMatrixCell:
    day_id: str
    period_id: str
    entries: tuple[TeacherTimetableEntry, ...]


@dataclass(frozen=True)
class TeacherMatrixTeacher:
    id: str
    name: str
    cells: tuple[TeacherMatrixCell, ...]
    """Occupied cells only; absence of a coordinate means free."""


@dataclass(frozen=True)
class TeacherTimetableMatrixView:
    school_id: str
    school_name: str
    academic_year_id: str
    academic_year_label: str
    version_number: int
    solver_status: SolverStatus
    total_soft_penalty: int
    created_at: datetime
    is_active: bool
    days: tuple[TeacherMatrixDay, ...]
    periods: tuple[TeacherMatrixPeriod, ...]
    teachers: tuple[TeacherMatrixTeacher, ...]
