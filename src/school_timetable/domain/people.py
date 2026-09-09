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
    """A scheduling reference entity only -- no email/phone/title/
    department/subject-specialization/login/contract-workload/
    availability fields (Owner Decision #37). The teacher-subject
    relationship is never stored here; it lives entirely in
    ``TeachingRequirement``.

    Both ``first_name`` and ``last_name`` are required constructor
    arguments -- every caller must provide both explicitly, with no
    dataclass default papering over the old single-``name`` shape.
    Persisted rows migrated from the old schema may legitimately carry
    an explicit ``last_name=""`` (the migration backfills it that way,
    non-heuristically, to preserve the old single-token display name
    byte-for-byte); that is a fact about migrated data, not a
    constructor convenience. The future Teacher CRUD (Slice B) owns
    real create/update input validation for genuinely new teachers.
    """

    id: str
    first_name: str
    last_name: str

    @property
    def full_name(self) -> str:
        """The one authoritative display name (Owner Decision #37) --
        every existing read API/UI that used to expose the old single
        `name` field now derives it from here, verbatim. Trims each
        part and joins with exactly one space; an empty `last_name`
        (the common case for migrated synthetic rows) never leaves a
        trailing or double space, so a pre-migration single-token name
        like "Teacher Math" survives as `full_name` byte-for-byte
        unchanged."""
        parts = [self.first_name.strip(), self.last_name.strip()]
        return " ".join(part for part in parts if part != "")


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
