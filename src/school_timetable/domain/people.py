"""Teachers and their availability."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class AvailabilityStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    PREFER_NOT = "PREFER_NOT"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True)
class Teacher:
    id: str
    name: str


@dataclass(frozen=True)
class TeacherAvailability:
    """An availability override for one teacher at one (day, period).

    Any (teacher, day, period) combination not covered by an explicit
    ``TeacherAvailability`` entry defaults to ``AVAILABLE``.
    """

    teacher_id: str
    day_id: str
    period_id: str
    status: AvailabilityStatus
