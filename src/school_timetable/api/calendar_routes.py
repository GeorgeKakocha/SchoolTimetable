"""`GET .../calendar`, `POST/PUT/DELETE .../calendar/days[/{day_id}]`,
`POST .../calendar/days/{day_id}/move`, and the Period equivalents
(Calendar A).

`Day`/`Period` are the existing `domain.calendar.Day`/`Period` -- not a
new/redesigned entity; this slice only adds the missing catalog write
surface over them, plus two new optional `Period.start_time`/`end_time`
fields. All routes depend only on `application/` Protocols/services
(`CalendarProjectionService`, `CalendarService`), never on a concrete
`persistence/` class -- the composition root wiring those concrete,
session-factory-backed adapters lives entirely in `api/dependencies.py`,
matching `resource_routes.py`'s exact discipline.

`school_id`/`year_id` are natural/domain IDs, never surrogate ones.

POST/PUT return the full written Day/Period's own resolved fields
directly. DELETE returns 200 with `{"deleted_id": ...}`. `move` returns
200 with the full, freshly-reprojected `CalendarProjectionResponse` --
unlike a single-row write, a reorder can change more than one row
(block recomputation can touch every Period), so returning the whole
projection avoids the caller needing a second GET.

Error mapping (mirrors `resource_routes.py`'s exact locked contract):
- `SchedulingProblemNotFoundError` -> 404, `{"detail": "Scheduling
  configuration not found"}`. All routes.
- `DayNotFoundError`/`PeriodNotFoundError` -> 404, `{"detail": "Day not
  found"}`/`{"detail": "Period not found"}`. PUT/DELETE/move only.
- `InvalidDayError`/`InvalidPeriodError` -> 422, `{"code":
  "INVALID_DAY"/"INVALID_PERIOD", "detail": "...", "errors": [...]}`.
- `DuplicateDayError`/`DuplicatePeriodError` -> 409, `{"code":
  "DUPLICATE_DAY"/"DUPLICATE_PERIOD", "detail": "..."}`.
- `DayInUseError`/`PeriodInUseError` -> 409, `{"code": "DAY_IN_USE"/
  "PERIOD_IN_USE", "detail": "...", "referenced_by": [...]}`.
- `PeriodReorderBlockedError` -> 409, `{"code":
  "PERIOD_REORDER_BLOCKED_BY_TIME_PREFERENCES", "detail": "..."}`.
- `ConfigurationLockedError` -> 409, `{"code":
  "SCHEDULING_CONFIGURATION_LOCKED", "detail": "..."}` -- the stable
  Decision #35 HTTP contract, reused verbatim.

Deliberately NOT caught here, by design: any other unexpected exception
-- an internal defect, left to reach FastAPI's normal unhandled-
exception (generic 500) behavior, matching every other route in this
API.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from school_timetable.api.dependencies import (
    get_calendar_projection_service,
    get_calendar_service,
)
from school_timetable.api.schemas import (
    CalendarProjectionResponse,
    ConfigurationLockedErrorResponse,
    DayDeleteResponse,
    DayInUseErrorResponse,
    DayMoveRequest,
    DayWriteRequest,
    DayWriteResponse,
    DuplicateDayErrorResponse,
    DuplicatePeriodErrorResponse,
    InvalidDayErrorResponse,
    InvalidPeriodErrorResponse,
    PeriodDeleteResponse,
    PeriodInUseErrorResponse,
    PeriodMoveRequest,
    PeriodReorderBlockedErrorResponse,
    PeriodWriteRequest,
    PeriodWriteResponse,
)
from school_timetable.api.serializer import (
    calendar_projection_response_from_view,
    day_write_response_from_result,
    parse_hhmm,
    period_write_response_from_result,
    validation_diagnostic_responses_from_warnings,
)
from school_timetable.application.calendar_models import DayFields, PeriodFields
from school_timetable.application.calendar_projection_service import CalendarProjectionService
from school_timetable.application.calendar_service import CalendarService
from school_timetable.application.errors import (
    ConfigurationLockedError,
    DayInUseError,
    DayNotFoundError,
    DuplicateDayError,
    DuplicatePeriodError,
    InvalidDayError,
    InvalidPeriodError,
    PeriodInUseError,
    PeriodNotFoundError,
    PeriodReorderBlockedError,
    SchedulingProblemNotFoundError,
)

router = APIRouter()


@router.get(
    "/schools/{school_id}/years/{year_id}/calendar",
    response_model=CalendarProjectionResponse,
)
def get_calendar(
    school_id: str,
    year_id: str,
    service: CalendarProjectionService = Depends(get_calendar_projection_service),
) -> CalendarProjectionResponse:
    try:
        view = service.project(school_id, year_id)
    except SchedulingProblemNotFoundError:
        raise HTTPException(status_code=404, detail="Scheduling configuration not found") from None
    return calendar_projection_response_from_view(view)


# -- Day ----------------------------------------------------------------


@router.post(
    "/schools/{school_id}/years/{year_id}/calendar/days",
    response_model=DayWriteResponse,
    status_code=201,
)
def create_day(
    school_id: str,
    year_id: str,
    body: DayWriteRequest,
    service: CalendarService = Depends(get_calendar_service),
):
    try:
        result = service.day_create(school_id, year_id, DayFields(name=body.name))
    except SchedulingProblemNotFoundError:
        raise HTTPException(status_code=404, detail="Scheduling configuration not found") from None
    except _DAY_WRITE_ERRORS as exc:
        return _day_write_error_response(exc)
    return day_write_response_from_result(result)


@router.put(
    "/schools/{school_id}/years/{year_id}/calendar/days/{day_id}",
    response_model=DayWriteResponse,
)
def update_day(
    school_id: str,
    year_id: str,
    day_id: str,
    body: DayWriteRequest,
    service: CalendarService = Depends(get_calendar_service),
):
    try:
        result = service.day_update(school_id, year_id, day_id, DayFields(name=body.name))
    except SchedulingProblemNotFoundError:
        raise HTTPException(status_code=404, detail="Scheduling configuration not found") from None
    except DayNotFoundError:
        raise HTTPException(status_code=404, detail="Day not found") from None
    except _DAY_WRITE_ERRORS as exc:
        return _day_write_error_response(exc)
    return day_write_response_from_result(result)


@router.delete(
    "/schools/{school_id}/years/{year_id}/calendar/days/{day_id}",
    response_model=DayDeleteResponse,
)
def delete_day(
    school_id: str,
    year_id: str,
    day_id: str,
    service: CalendarService = Depends(get_calendar_service),
):
    try:
        service.day_delete(school_id, year_id, day_id)
    except SchedulingProblemNotFoundError:
        raise HTTPException(status_code=404, detail="Scheduling configuration not found") from None
    except DayNotFoundError:
        raise HTTPException(status_code=404, detail="Day not found") from None
    except _DAY_WRITE_ERRORS as exc:
        return _day_write_error_response(exc)
    return DayDeleteResponse(deleted_id=day_id)


@router.post(
    "/schools/{school_id}/years/{year_id}/calendar/days/{day_id}/move",
    response_model=CalendarProjectionResponse,
)
def move_day(
    school_id: str,
    year_id: str,
    day_id: str,
    body: DayMoveRequest,
    calendar_service: CalendarService = Depends(get_calendar_service),
    projection_service: CalendarProjectionService = Depends(get_calendar_projection_service),
):
    try:
        calendar_service.day_move(school_id, year_id, day_id, body.direction)
    except SchedulingProblemNotFoundError:
        raise HTTPException(status_code=404, detail="Scheduling configuration not found") from None
    except DayNotFoundError:
        raise HTTPException(status_code=404, detail="Day not found") from None
    except _DAY_WRITE_ERRORS as exc:
        return _day_write_error_response(exc)
    return calendar_projection_response_from_view(projection_service.project(school_id, year_id))


# -- Period ---------------------------------------------------------------


@router.post(
    "/schools/{school_id}/years/{year_id}/calendar/periods",
    response_model=PeriodWriteResponse,
    status_code=201,
)
def create_period(
    school_id: str,
    year_id: str,
    body: PeriodWriteRequest,
    service: CalendarService = Depends(get_calendar_service),
):
    try:
        fields = _period_fields_from_request(body, school_id, year_id)
        result = service.period_create(school_id, year_id, fields)
    except SchedulingProblemNotFoundError:
        raise HTTPException(status_code=404, detail="Scheduling configuration not found") from None
    except _PERIOD_WRITE_ERRORS as exc:
        return _period_write_error_response(exc)
    return period_write_response_from_result(result)


@router.put(
    "/schools/{school_id}/years/{year_id}/calendar/periods/{period_id}",
    response_model=PeriodWriteResponse,
)
def update_period(
    school_id: str,
    year_id: str,
    period_id: str,
    body: PeriodWriteRequest,
    service: CalendarService = Depends(get_calendar_service),
):
    try:
        fields = _period_fields_from_request(body, school_id, year_id)
        result = service.period_update(school_id, year_id, period_id, fields)
    except SchedulingProblemNotFoundError:
        raise HTTPException(status_code=404, detail="Scheduling configuration not found") from None
    except PeriodNotFoundError:
        raise HTTPException(status_code=404, detail="Period not found") from None
    except _PERIOD_WRITE_ERRORS as exc:
        return _period_write_error_response(exc)
    return period_write_response_from_result(result)


@router.delete(
    "/schools/{school_id}/years/{year_id}/calendar/periods/{period_id}",
    response_model=PeriodDeleteResponse,
)
def delete_period(
    school_id: str,
    year_id: str,
    period_id: str,
    service: CalendarService = Depends(get_calendar_service),
):
    try:
        service.period_delete(school_id, year_id, period_id)
    except SchedulingProblemNotFoundError:
        raise HTTPException(status_code=404, detail="Scheduling configuration not found") from None
    except PeriodNotFoundError:
        raise HTTPException(status_code=404, detail="Period not found") from None
    except _PERIOD_WRITE_ERRORS as exc:
        return _period_write_error_response(exc)
    return PeriodDeleteResponse(deleted_id=period_id)


@router.post(
    "/schools/{school_id}/years/{year_id}/calendar/periods/{period_id}/move",
    response_model=CalendarProjectionResponse,
)
def move_period(
    school_id: str,
    year_id: str,
    period_id: str,
    body: PeriodMoveRequest,
    calendar_service: CalendarService = Depends(get_calendar_service),
    projection_service: CalendarProjectionService = Depends(get_calendar_projection_service),
):
    try:
        calendar_service.period_move(school_id, year_id, period_id, body.direction)
    except SchedulingProblemNotFoundError:
        raise HTTPException(status_code=404, detail="Scheduling configuration not found") from None
    except PeriodNotFoundError:
        raise HTTPException(status_code=404, detail="Period not found") from None
    except _PERIOD_WRITE_ERRORS as exc:
        return _period_write_error_response(exc)
    return calendar_projection_response_from_view(projection_service.project(school_id, year_id))


def _period_fields_from_request(body: PeriodWriteRequest, school_id: str, year_id: str) -> PeriodFields:
    return PeriodFields(
        name=body.name,
        start_time=parse_hhmm(body.start_time, school_id, year_id),
        end_time=parse_hhmm(body.end_time, school_id, year_id),
        starts_new_block=body.starts_new_block,
    )


_DAY_WRITE_ERRORS = (InvalidDayError, DuplicateDayError, DayInUseError, ConfigurationLockedError)


def _day_write_error_response(exc: Exception) -> JSONResponse:
    """Shared by create/update/delete/move -- one dispatch table from
    exception type to its locked `{status_code, body}` contract, never
    duplicated per route."""
    if isinstance(exc, InvalidDayError):
        return JSONResponse(
            status_code=422,
            content=InvalidDayErrorResponse(
                code="INVALID_DAY", detail=str(exc),
                errors=validation_diagnostic_responses_from_warnings(exc.validation_errors),
            ).model_dump(),
        )
    if isinstance(exc, DuplicateDayError):
        return JSONResponse(
            status_code=409,
            content=DuplicateDayErrorResponse(code="DUPLICATE_DAY", detail=str(exc)).model_dump(),
        )
    if isinstance(exc, DayInUseError):
        return JSONResponse(
            status_code=409,
            content=DayInUseErrorResponse(
                code="DAY_IN_USE", detail=str(exc), referenced_by=exc.referenced_by,
            ).model_dump(),
        )
    # ConfigurationLockedError -- the last member of `_DAY_WRITE_ERRORS`.
    return JSONResponse(
        status_code=409,
        content=ConfigurationLockedErrorResponse(
            code="SCHEDULING_CONFIGURATION_LOCKED",
            detail="Scheduling configuration is locked because a schedule already exists",
        ).model_dump(),
    )


_PERIOD_WRITE_ERRORS = (
    InvalidPeriodError,
    DuplicatePeriodError,
    PeriodInUseError,
    PeriodReorderBlockedError,
    ConfigurationLockedError,
)


def _period_write_error_response(exc: Exception) -> JSONResponse:
    """Shared by create/update/delete/move -- one dispatch table from
    exception type to its locked `{status_code, body}` contract, never
    duplicated per route."""
    if isinstance(exc, InvalidPeriodError):
        return JSONResponse(
            status_code=422,
            content=InvalidPeriodErrorResponse(
                code="INVALID_PERIOD", detail=str(exc),
                errors=validation_diagnostic_responses_from_warnings(exc.validation_errors),
            ).model_dump(),
        )
    if isinstance(exc, DuplicatePeriodError):
        return JSONResponse(
            status_code=409,
            content=DuplicatePeriodErrorResponse(code="DUPLICATE_PERIOD", detail=str(exc)).model_dump(),
        )
    if isinstance(exc, PeriodInUseError):
        return JSONResponse(
            status_code=409,
            content=PeriodInUseErrorResponse(
                code="PERIOD_IN_USE", detail=str(exc), referenced_by=exc.referenced_by,
            ).model_dump(),
        )
    if isinstance(exc, PeriodReorderBlockedError):
        return JSONResponse(
            status_code=409,
            content=PeriodReorderBlockedErrorResponse(
                code="PERIOD_REORDER_BLOCKED_BY_TIME_PREFERENCES",
                detail="Periods cannot be reordered while teaching requirements contain time preferences.",
            ).model_dump(),
        )
    # ConfigurationLockedError -- the last member of `_PERIOD_WRITE_ERRORS`.
    return JSONResponse(
        status_code=409,
        content=ConfigurationLockedErrorResponse(
            code="SCHEDULING_CONFIGURATION_LOCKED",
            detail="Scheduling configuration is locked because a schedule already exists",
        ).model_dump(),
    )
