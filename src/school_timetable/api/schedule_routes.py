"""`GET /schools/{school_id}/years/{year_id}/schedule/active`,
`POST /schools/{school_id}/years/{year_id}/schedule/generate` (Phase
3A3.4, `docs/DECISIONS.md` #31's locked HTTP contract), and
`GET /schools/{school_id}/years/{year_id}/schedule/active/classes/{class_section_id}`
(Phase 3B.1, `docs/DECISIONS.md` #32's locked HTTP contract).

All three routes depend only on `application/` Protocols/services
(`ScheduleVersionRepository`, `GenerateScheduleService`,
`ClassTimetableService`), never on a concrete `persistence/` class --
the composition root wiring those concrete, session-factory-backed
adapters lives entirely in `api/dependencies.py`.

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
- `ConfigurationChangedDuringGenerationError` (Owner Decision #36, Phase
  3C.2b) -> 409, `{"code": "CONFIGURATION_CHANGED_DURING_GENERATION",
  "detail": "..."}` -- a deliberate, retryable correctness condition (a
  Phase 3C.2 configuration write committed between this generation's
  load and its final lock-protected recheck; zero rows were persisted),
  never allowed to fall through as a generic 500.
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

The class-timetable projection route's error mapping (also locked, no
remaining owner decisions): `SchedulingProblemNotFoundError` -> 404
(same body as above); `ClassTimetableService.project` returning `None`
(no active `Schedule` yet) -> 404, `{"detail": "Active schedule not
found"}` -- the identical body the plain `.../schedule/active` route
already uses for the same underlying state; `ClassSectionNotFoundError`
-> 404, `{"detail": "Class section not found"}` -- a third, distinct,
still code-less 404. No new stable `code` is introduced for any of
these three. Any other unexpected exception (including a corrupt
persisted `Schedule` state) is, again, deliberately not caught here.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from school_timetable.api.dependencies import (
    get_class_timetable_service,
    get_generate_schedule_service,
    get_schedule_version_repository,
)
from school_timetable.api.schemas import (
    ActiveScheduleResponse,
    ClassTimetableResponse,
    GenerateScheduleResponse,
    GenerationErrorResponse,
    InvalidConfigurationResponse,
)
from school_timetable.api.serializer import (
    active_schedule_response_from_active_version,
    class_timetable_response_from_view,
    generate_response_from_active_version,
    validation_diagnostic_response_from_error,
)
from school_timetable.application.class_timetable_service import ClassTimetableService
from school_timetable.application.errors import (
    ClassSectionNotFoundError,
    ConfigurationChangedDuringGenerationError,
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
    except ConfigurationChangedDuringGenerationError:
        return JSONResponse(
            status_code=409,
            content=GenerationErrorResponse(
                code="CONFIGURATION_CHANGED_DURING_GENERATION",
                detail="Scheduling configuration changed during generation; retry generation",
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


@router.get(
    "/schools/{school_id}/years/{year_id}/schedule/active/classes/{class_section_id}",
    response_model=ClassTimetableResponse,
)
def get_class_timetable(
    school_id: str,
    year_id: str,
    class_section_id: str,
    service: ClassTimetableService = Depends(get_class_timetable_service),
) -> ClassTimetableResponse:
    try:
        view = service.project(school_id, year_id, class_section_id)
    except SchedulingProblemNotFoundError:
        raise HTTPException(status_code=404, detail="Scheduling configuration not found") from None
    except ClassSectionNotFoundError:
        raise HTTPException(status_code=404, detail="Class section not found") from None
    if view is None:
        raise HTTPException(status_code=404, detail="Active schedule not found")
    return class_timetable_response_from_view(view)
