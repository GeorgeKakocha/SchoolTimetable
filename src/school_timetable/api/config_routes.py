"""GET /schools/{school_id}/years/{year_id}/config (Phase 3A2.4).

Read-only: loads one school's one academic year's persisted scheduling
configuration and returns it as the explicit, hand-designed
`SchedulingConfigResponse` contract -- never an ORM row, never a
persistence surrogate ID. No solver/preflight is invoked here; that
proof already exists in Phase 3A2.3's repository round-trip test.
`school_id`/`year_id` are natural/domain IDs, never surrogate ones.
`SchedulingProblemNotFoundError` is mapped to a generic 404, identically
whether the school or the academic year is the part that doesn't
resolve -- see `docs/DECISIONS.md` #29. No other exception is caught
here: an unexpected failure must surface as a 500, not be mistaken for
a legitimate not-found result.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from school_timetable.api.dependencies import get_scheduling_problem_repository
from school_timetable.api.schemas import SchedulingConfigResponse
from school_timetable.api.serializer import config_response_from_problem
from school_timetable.application.errors import SchedulingProblemNotFoundError
from school_timetable.application.ports import SchedulingProblemRepository

router = APIRouter()


@router.get("/schools/{school_id}/years/{year_id}/config", response_model=SchedulingConfigResponse)
def get_scheduling_config(
    school_id: str,
    year_id: str,
    repository: SchedulingProblemRepository = Depends(get_scheduling_problem_repository),
) -> SchedulingConfigResponse:
    try:
        problem = repository.load_by_school_and_year(school_id, year_id)
    except SchedulingProblemNotFoundError:
        raise HTTPException(status_code=404, detail="Scheduling configuration not found") from None
    return config_response_from_problem(problem)
