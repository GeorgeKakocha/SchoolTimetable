"""`GET/POST /schools/{school_id}/years/{year_id}/reserved-activities`
and `PUT/DELETE .../reserved-activities/{reserved_activity_id}`
(Reserved Activities Slice A2).

"Reserved Activity" is not a new domain entity -- it is the
user-facing name for `ReservedBlock` (see `domain/blocks.py`). All
four routes depend only on `application/` Protocols/services
(`ReservedActivityProjectionService`, `ReservedActivityService`),
never on a concrete `persistence/` class -- the composition root
wiring those concrete, session-factory-backed adapters lives entirely
in `api/dependencies.py`, matching `special_activity_routes.py`'s
exact discipline.

`school_id`/`year_id` are natural/domain IDs, never surrogate ones.

POST/PUT return the full written Reserved Activity's own
canonically-ordered fields directly (never `name`, which is
server-derived and never independently exposed) -- the caller still
re-fetches `GET .../reserved-activities` for page-level state
(`configuration_locked`, full catalogs, full ordered list). DELETE
returns 200 with `{"deleted_id": ...}`.

Error mapping (mirrors every other Real-School Setup MVP / Reserved
Activities write surface's locked contract):
- `SchedulingProblemNotFoundError` -> 404, `{"detail": "Scheduling
  configuration not found"}`. All four routes.
- `ReservedActivityNotFoundError` -> 404, `{"detail": "Reserved
  activity not found"}` -- the top-level PUT/DELETE *target* itself
  not existing, distinct from a nested reference not resolving.
  PUT/DELETE only.
- `UnknownReferenceError` -> 422, `{"code": "UNKNOWN_REFERENCE",
  "detail": "...", "reference_kind": "...", "reference_id": "..."}` --
  a nested `special_activity_id`/`class_section_id`/`teacher_id`/
  `day_id`/`period_id` not resolving. POST/PUT.
- `NonSpecialActivityTargetError` -> 422, `{"code":
  "NON_SPECIAL_ACTIVITY_TARGET", "detail": "Selected activity is not a
  Special Activity.", "activity_id": "..."}` -- the nested
  `special_activity_id` exists but is not a Special Activity. Never
  carries `actual_kind`/`kind`/raw `CLUB`/`ORDINARY` vocabulary,
  deliberately never reusing Teaching Assignments' unrelated
  `NonOrdinaryActivityTargetError`/`NON_ORDINARY_ACTIVITY_TARGET`.
  POST/PUT.
- `InvalidReservedActivityError` -> 422, `{"code":
  "INVALID_RESERVED_ACTIVITY", "detail": "...", "errors": [...]}` --
  empty class/slot collections, in-request duplicates, a
  non-instructional slot, a Teacher-`UNAVAILABLE` slot, or a
  class/teacher cross-block collision. POST/PUT.
- `ConfigurationLockedError` -> 409, `{"code":
  "SCHEDULING_CONFIGURATION_LOCKED", "detail": "Scheduling
  configuration is locked because a schedule already exists"}` --
  reused verbatim. POST/PUT/DELETE.

Deliberately NOT caught here, by design: any other unexpected exception
-- an internal defect, never a client-actionable outcome, left to reach
FastAPI's normal unhandled-exception (generic 500) behavior, matching
every other route in this API.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from school_timetable.api.dependencies import get_reserved_activities_projection_service, get_reserved_activity_service
from school_timetable.api.schemas import (
    ConfigurationLockedErrorResponse,
    InvalidReservedActivityErrorResponse,
    NonSpecialActivityTargetErrorResponse,
    ReservedActivitiesProjectionResponse,
    ReservedActivityDeleteResponse,
    ReservedActivitySlotResponse,
    ReservedActivityWriteRequest,
    ReservedActivityWriteResponse,
    UnknownReferenceErrorResponse,
)
from school_timetable.api.serializer import (
    reserved_activities_projection_response_from_view,
    validation_diagnostic_responses_from_warnings,
)
from school_timetable.application.errors import (
    ConfigurationLockedError,
    InvalidReservedActivityError,
    NonSpecialActivityTargetError,
    ReservedActivityNotFoundError,
    SchedulingProblemNotFoundError,
    UnknownReferenceError,
)
from school_timetable.application.reserved_activity_models import ReservedActivityFields, ReservedActivitySlotFields
from school_timetable.application.reserved_activity_projection_service import ReservedActivityProjectionService
from school_timetable.application.reserved_activity_service import ReservedActivityService

router = APIRouter()


@router.get(
    "/schools/{school_id}/years/{year_id}/reserved-activities",
    response_model=ReservedActivitiesProjectionResponse,
)
def get_reserved_activities(
    school_id: str,
    year_id: str,
    service: ReservedActivityProjectionService = Depends(get_reserved_activities_projection_service),
) -> ReservedActivitiesProjectionResponse:
    try:
        view = service.project(school_id, year_id)
    except SchedulingProblemNotFoundError:
        raise HTTPException(status_code=404, detail="Scheduling configuration not found") from None
    return reserved_activities_projection_response_from_view(view)


@router.post(
    "/schools/{school_id}/years/{year_id}/reserved-activities",
    response_model=ReservedActivityWriteResponse,
    status_code=201,
)
def create_reserved_activity(
    school_id: str,
    year_id: str,
    body: ReservedActivityWriteRequest,
    service: ReservedActivityService = Depends(get_reserved_activity_service),
):
    try:
        result = service.create(school_id, year_id, _fields_from_request(body))
    except SchedulingProblemNotFoundError:
        raise HTTPException(status_code=404, detail="Scheduling configuration not found") from None
    except _WRITE_ERRORS as exc:
        return _write_error_response(exc)
    return _response_from_result(result)


@router.put(
    "/schools/{school_id}/years/{year_id}/reserved-activities/{reserved_activity_id}",
    response_model=ReservedActivityWriteResponse,
)
def update_reserved_activity(
    school_id: str,
    year_id: str,
    reserved_activity_id: str,
    body: ReservedActivityWriteRequest,
    service: ReservedActivityService = Depends(get_reserved_activity_service),
):
    try:
        result = service.update(school_id, year_id, reserved_activity_id, _fields_from_request(body))
    except SchedulingProblemNotFoundError:
        raise HTTPException(status_code=404, detail="Scheduling configuration not found") from None
    except ReservedActivityNotFoundError:
        raise HTTPException(status_code=404, detail="Reserved activity not found") from None
    except _WRITE_ERRORS as exc:
        return _write_error_response(exc)
    return _response_from_result(result)


@router.delete(
    "/schools/{school_id}/years/{year_id}/reserved-activities/{reserved_activity_id}",
    response_model=ReservedActivityDeleteResponse,
)
def delete_reserved_activity(
    school_id: str,
    year_id: str,
    reserved_activity_id: str,
    service: ReservedActivityService = Depends(get_reserved_activity_service),
):
    try:
        service.delete(school_id, year_id, reserved_activity_id)
    except SchedulingProblemNotFoundError:
        raise HTTPException(status_code=404, detail="Scheduling configuration not found") from None
    except ReservedActivityNotFoundError:
        raise HTTPException(status_code=404, detail="Reserved activity not found") from None
    except _WRITE_ERRORS as exc:
        return _write_error_response(exc)
    return ReservedActivityDeleteResponse(deleted_id=reserved_activity_id)


def _fields_from_request(body: ReservedActivityWriteRequest) -> ReservedActivityFields:
    return ReservedActivityFields(
        special_activity_id=body.special_activity_id,
        class_section_ids=body.class_section_ids,
        teacher_id=body.teacher_id,
        slots=tuple(ReservedActivitySlotFields(day_id=s.day_id, period_id=s.period_id) for s in body.slots),
    )


def _response_from_result(result) -> ReservedActivityWriteResponse:
    return ReservedActivityWriteResponse(
        id=result.id,
        special_activity_id=result.special_activity_id,
        class_section_ids=result.class_section_ids,
        teacher_id=result.teacher_id,
        slots=tuple(ReservedActivitySlotResponse(day_id=s.day_id, period_id=s.period_id) for s in result.slots),
    )


_WRITE_ERRORS = (
    UnknownReferenceError,
    NonSpecialActivityTargetError,
    InvalidReservedActivityError,
    ConfigurationLockedError,
)


def _write_error_response(exc: Exception) -> JSONResponse:
    """Shared by create/update/delete -- one dispatch table from
    exception type to its locked `{status_code, body}` contract, never
    duplicated per route."""
    if isinstance(exc, UnknownReferenceError):
        return JSONResponse(
            status_code=422,
            content=UnknownReferenceErrorResponse(
                code="UNKNOWN_REFERENCE",
                detail=str(exc),
                reference_kind=exc.reference_kind,
                reference_id=exc.reference_id,
            ).model_dump(),
        )
    if isinstance(exc, NonSpecialActivityTargetError):
        return JSONResponse(
            status_code=422,
            content=NonSpecialActivityTargetErrorResponse(
                code="NON_SPECIAL_ACTIVITY_TARGET",
                detail="Selected activity is not a Special Activity.",
                activity_id=exc.activity_id,
            ).model_dump(),
        )
    if isinstance(exc, InvalidReservedActivityError):
        return JSONResponse(
            status_code=422,
            content=InvalidReservedActivityErrorResponse(
                code="INVALID_RESERVED_ACTIVITY",
                detail=str(exc),
                errors=validation_diagnostic_responses_from_warnings(exc.validation_errors),
            ).model_dump(),
        )
    # ConfigurationLockedError -- the last member of `_WRITE_ERRORS`.
    return JSONResponse(
        status_code=409,
        content=ConfigurationLockedErrorResponse(
            code="SCHEDULING_CONFIGURATION_LOCKED",
            detail="Scheduling configuration is locked because a schedule already exists",
        ).model_dump(),
    )
