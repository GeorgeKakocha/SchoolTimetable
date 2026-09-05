"""`GET /schools/{school_id}/years/{year_id}/schedule/active` and
`POST /schools/{school_id}/years/{year_id}/schedule/generate` (Phase
3A3.4, `docs/DECISIONS.md` #31's locked HTTP contract).

Both routes depend only on `application/` Protocols/services
(`ScheduleVersionRepository`, `GenerateScheduleService`), never on a
concrete `persistence/` class -- the composition root wiring those
concrete, session-factory-backed adapters lives entirely in
`api/dependencies.py`.

`school_id`/`year_id` are natural/domain IDs, never surrogate ones,
matching `/config`'s existing convention exactly.

Error mapping (locked, no remaining owner decisions):
- `SchedulingProblemNotFoundError` -> 404, `{"detail": "Scheduling
  configuration not found"}` -- the exact existing `/config` body,
  unchanged, no `code` field.
- (GET only) no active `Schedule` yet (`get_active_schedule` returned
  `None`) -> 404, `{"detail": "Active schedule not found"}` -- a
  distinct state from the one above, still no `code` field, never
  translated to/treated as `SchedulingProblemNotFoundError`.
- `ScheduleAlreadyExistsError` -> 409, `{"code":
  "SCHEDULE_ALREADY_EXISTS", "detail": "..."}`.
- `ScheduleInfeasibleError` -> 409, `{"code": "SCHEDULE_INFEASIBLE",
  "detail": "..."}`.
- `InvalidSchedulingConfigurationError` -> 422, `{"code":
  "INVALID_CONFIGURATION", "detail": "...", "errors": [...]}` -- the
  validator's own diagnostics, in their original order.

Deliberately NOT caught here, by design: `ScheduleGenerationError`,
`ScheduleVerificationFailedError`, `persistence.schedule_repository.
CorruptScheduleStateError`, or any other unexpected exception -- these
are internal defects, never a client-actionable outcome, and are left
to reach FastAPI's normal unhandled-exception (generic 500) behavior
rather than importing persistence-internal defect types into this
route merely to translate them.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from school_timetable.api.dependencies import get_generate_schedule_service, get_schedule_version_repository
from school_timetable.api.schemas import (
    ActiveScheduleResponse,
    GenerateScheduleResponse,
    GenerationErrorResponse,
    InvalidConfigurationResponse,
)
from school_timetable.api.serializer import (
    active_schedule_response_from_active_version,
    generate_response_from_active_version,
    validation_diagnostic_response_from_error,
)
from school_timetable.application.errors import (
    InvalidSchedulingConfigurationError,
    ScheduleAlreadyExistsError,
    ScheduleInfeasibleError,
    SchedulingProblemNotFoundError,
)
from school_timetable.application.generate_schedule_service import GenerateScheduleService
from school_timetable.application.ports import ScheduleVersionRepository

router = APIRouter()


@router.get("/schools/{school_id}/years/{year_id}/schedule/active", response_model=ActiveScheduleResponse)
def get_active_schedule(
    school_id: str,
    year_id: str,
    repository: ScheduleVersionRepository = Depends(get_schedule_version_repository),
) -> ActiveScheduleResponse:
    try:
        active = repository.get_active_schedule(school_id, year_id)
    except SchedulingProblemNotFoundError:
        raise HTTPException(status_code=404, detail="Scheduling configuration not found") from None
    if active is None:
        raise HTTPException(status_code=404, detail="Active schedule not found")
    return active_schedule_response_from_active_version(active)


@router.post(
    "/schools/{school_id}/years/{year_id}/schedule/generate",
    response_model=GenerateScheduleResponse,
    status_code=201,
)
def generate_schedule(
    school_id: str,
    year_id: str,
    service: GenerateScheduleService = Depends(get_generate_schedule_service),
):
    try:
        active = service.generate(school_id, year_id)
    except SchedulingProblemNotFoundError:
        raise HTTPException(status_code=404, detail="Scheduling configuration not found") from None
    except ScheduleAlreadyExistsError:
        return JSONResponse(
            status_code=409,
            content=GenerationErrorResponse(
                code="SCHEDULE_ALREADY_EXISTS",
                detail="A schedule already exists for this school and academic year",
            ).model_dump(),
        )
    except ScheduleInfeasibleError:
        return JSONResponse(
            status_code=409,
            content=GenerationErrorResponse(
                code="SCHEDULE_INFEASIBLE",
                detail="No feasible schedule exists for this school and academic year",
            ).model_dump(),
        )
    except InvalidSchedulingConfigurationError as exc:
        return JSONResponse(
            status_code=422,
            content=InvalidConfigurationResponse(
                code="INVALID_CONFIGURATION",
                detail="Scheduling configuration is invalid",
                errors=tuple(
                    validation_diagnostic_response_from_error(e) for e in exc.validation_errors
                ),
            ).model_dump(),
        )
    return generate_response_from_active_version(active)
