"""Internal configuration snapshot used for regeneration concurrency control."""
from dataclasses import dataclass

from school_timetable.domain.problem import SchedulingProblem


@dataclass(frozen=True)
class DraftConfigurationSnapshot:
    revision_id: int
    problem: SchedulingProblem
