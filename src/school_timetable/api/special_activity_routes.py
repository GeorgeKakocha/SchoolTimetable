"""`GET/POST /schools/{school_id}/years/{year_id}/special-activities` and
`PUT/DELETE .../special-activities/{special_activity_id}` (Reserved
Activities Slice A1).

"Special Activity" is not a new domain entity -- it is the user-facing
name for `Activity(kind=CLUB)` (see `domain/activities.py`). All four
routes depend only on `application/` Protocols/services
(`SpecialActivityProjectionService`, `SpecialActivityService`), never
on a concrete `persistence/` class -- the composition root wiring
those concrete, session-factory-backed adapters lives entirely in
`api/dependencies.py`, matching `subject_routes.py`'s exact
discipline.

`school_id`/`year_id` are natural/domain IDs, never surrogate ones,
matching every other route's existing convention.

POST/PUT return the full written Special Activity's own resolved
fields directly -- a Special Activity write affects nothing but its
own row and (on rename) every `ReservedBlock.name` mirroring it, so
there is no larger page projection to protect against staleness by
withholding fields; the caller must still re-fetch
`GET .../special-activities` for page-level state
(`configuration_locked`, full ordered list). DELETE returns 200 with
`{"deleted_id": ...}`, no `warnings` field -- Special Activity writes
have no TeachingAssignment-style quantitative warning mechanism.

Error mapping (mirrors `subject_routes.py`'s exact locked contract):
- `SchedulingProblemNotFoundError` -> 404, `{"detail": "Scheduling
  configuration not found"}` -- the exact existing body, unchanged, no
  `code` field. All four routes.
- `SpecialActivityNotFoundError` -> 404, `{"detail": "Special activity
  not found"}` -- raised identically whether `special_activity_id`
  does not exist at all, or it exists but is `ActivityKind.ORDINARY`;
  an ORDINARY activity is never distinguishable from a missing Special
  Activity through this filtered resource surface. PUT/DELETE only.
- `InvalidSpecialActivityError` -> 422, `{"code":
  "INVALID_SPECIAL_ACTIVITY", "detail": "...", "errors": [...]}` --
  mirrors `InvalidSubjectError` exactly. POST/PUT.
- `DuplicateSpecialActivityError` -> 409, `{"code":
  "DUPLICATE_SPECIAL_ACTIVITY", "detail": "..."}` -- mirrors
  `DuplicateSubjectError`'s existing 409 precedent (a conflict with
  existing state, not malformed input). POST/PUT.
- `SpecialActivityInUseError` -> 409, `{"code":
  "SPECIAL_ACTIVITY_IN_USE", "detail": "...", "referenced_by": [...]}`.
  DELETE.
- `ConfigurationLockedError` -> 409, `{"code":
  "SCHEDULING_CONFIGURATION_LOCKED", "detail": "Scheduling
  configuration is locked because a schedule already exists"}` -- the
  stable Decision #35 HTTP contract, reused verbatim. POST/PUT/DELETE.

Deliberately NOT caught here, by design: any other unexpected exception
-- an internal defect, never a client-actionable outcome, left to reach
FastAPI's normal unhandled-exception (generic 500) behavior, matching
every other route in this API. `ConfigurationChangedDuringGenerationError`
is never mapped here -- it belongs solely to generation's own final
persist path.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from school_timetable.api.dependencies import get_special_activities_projection_service, get_special_activity_service
from school_timetable.api.schemas import (
    ConfigurationLockedErrorResponse,
    DuplicateSpecialActivityErrorResponse,
    InvalidSpecialActivityErrorResponse,
    SpecialActivitiesProjectionResponse,
    SpecialActivityDeleteResponse,
    SpecialActivityInUseErrorResponse,
    SpecialActivityWriteRequest,
    SpecialActivityWriteResponse,
)
from school_timetable.api.serializer import (
    special_activities_projection_response_from_view,
    validation_diagnostic_responses_from_warnings,
)
from school_timetable.application.errors import (
    ConfigurationLockedError,
    DuplicateSpecialActivityError,
    InvalidSpecialActivityError,
    SchedulingProblemNotFoundError,
    SpecialActivityInUseError,
    SpecialActivityNotFoundError,
)
from school_timetable.application.special_activity_models import SpecialActivityFields
from school_timetable.application.special_activity_projection_service import SpecialActivityProjectionService
from school_timetable.application.special_activity_service import SpecialActivityService

router = APIRouter()


@router.get(
    "/schools/{school_id}/years/{year_id}/special-activities",
    response_model=SpecialActivitiesProjectionResponse,
)
def get_special_activities(
    school_id: str,
    year_id: str,
    service: SpecialActivityProjectionService = Depends(get_special_activities_projection_service),
) -> SpecialActivitiesProjectionResponse:
    try:
        view = service.project(school_id, year_id)
    except SchedulingProblemNotFoundError:
        raise HTTPException(status_code=404, detail="Scheduling configuration not found") from None
    return special_activities_projection_response_from_view(view)


@router.post(
    "/schools/{school_id}/years/{year_id}/special-activities",
    response_model=SpecialActivityWriteResponse,
    status_code=201,
)
def create_special_activity(
    school_id: str,
    year_id: str,
    body: SpecialActivityWriteRequest,
    service: SpecialActivityService = Depends(get_special_activity_service),
):
    try:
        result = service.create(school_id, year_id, _fields_from_request(body))
    except SchedulingProblemNotFoundError:
        raise HTTPException(status_code=404, detail="Scheduling configuration not found") from None
    except _WRITE_ERRORS as exc:
        return _write_error_response(exc)
    return SpecialActivityWriteResponse(id=result.id, name=result.name)


@router.put(
    "/schools/{school_id}/years/{year_id}/special-activities/{special_activity_id}",
    response_model=SpecialActivityWriteResponse,
)
def update_special_activity(
    school_id: str,
    year_id: str,
    special_activity_id: str,
    body: SpecialActivityWriteRequest,
    service: SpecialActivityService = Depends(get_special_activity_service),
):
    try:
        result = service.update(school_id, year_id, special_activity_id, _fields_from_request(body))
    except SchedulingProblemNotFoundError:
        raise HTTPException(status_code=404, detail="Scheduling configuration not found") from None
    except SpecialActivityNotFoundError:
        raise HTTPException(status_code=404, detail="Special activity not found") from None
    except _WRITE_ERRORS as exc:
        return _write_error_response(exc)
    return SpecialActivityWriteResponse(id=result.id, name=result.name)


@router.delete(
    "/schools/{school_id}/years/{year_id}/special-activities/{special_activity_id}",
    response_model=SpecialActivityDeleteResponse,
)
def delete_special_activity(
    school_id: str,
    year_id: str,
    special_activity_id: str,
    service: SpecialActivityService = Depends(get_special_activity_service),
):
    try:
        service.delete(school_id, year_id, special_activity_id)
    except SchedulingProblemNotFoundError:
        raise HTTPException(status_code=404, detail="Scheduling configuration not found") from None
    except SpecialActivityNotFoundError:
        raise HTTPException(status_code=404, detail="Special activity not found") from None
    except _WRITE_ERRORS as exc:
        return _write_error_response(exc)
    return SpecialActivityDeleteResponse(deleted_id=special_activity_id)


def _fields_from_request(body: SpecialActivityWriteRequest) -> SpecialActivityFields:
    return SpecialActivityFields(name=body.name)


_WRITE_ERRORS = (
    InvalidSpecialActivityError,
    DuplicateSpecialActivityError,
    SpecialActivityInUseError,
    ConfigurationLockedError,
)


def _write_error_response(exc: Exception) -> JSONResponse:
    """Shared by create/update/delete -- one dispatch table from
    exception type to its locked `{status_code, body}` contract, never
    duplicated per route."""
    if isinstance(exc, InvalidSpecialActivityError):
        return JSONResponse(
            status_code=422,
            content=InvalidSpecialActivityErrorResponse(
                code="INVALID_SPECIAL_ACTIVITY",
                detail=str(exc),
                errors=validation_diagnostic_responses_from_warnings(exc.validation_errors),
            ).model_dump(),
        )
    if isinstance(exc, DuplicateSpecialActivityError):
        return JSONResponse(
            status_code=409,
            content=DuplicateSpecialActivityErrorResponse(
                code="DUPLICATE_SPECIAL_ACTIVITY", detail=str(exc),
            ).model_dump(),
        )
    if isinstance(exc, SpecialActivityInUseError):
        return JSONResponse(
            status_code=409,
            content=SpecialActivityInUseErrorResponse(
                code="SPECIAL_ACTIVITY_IN_USE",
                detail=str(exc),
                referenced_by=exc.referenced_by,
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
