"""`GET /schools/{school_id}/years/{year_id}/teacher-availability` and
`PUT .../teacher-availability/{teacher_id}` (Owner Decision #38).

Both routes depend only on `application/` Protocols/services
(`TeacherAvailabilityProjectionService`, `TeacherAvailabilityService`),
never on a concrete `persistence/` class -- the composition root wiring
those concrete, session-factory-backed adapters lives entirely in
`api/dependencies.py`, matching every other route family's exact
discipline.

`school_id`/`year_id`/`teacher_id` are natural/domain IDs, never
surrogate ones. PUT returns the written Teacher's own resolved
exception set directly (mirrors `TeacherWriteResponse`) -- the caller
must still re-fetch `GET .../teacher-availability` for page-level
state (`configuration_locked`, every Teacher's exceptions).

No POST/PATCH/DELETE, and no per-cell/`/available`/`/unavailable`/
`/prefer-not` subroutes -- the write unit is always one Teacher's
complete sparse exception set (Owner Decision #38).

Error mapping:
- `SchedulingProblemNotFoundError` -> 404, `{"detail": "Scheduling
  configuration not found"}` -- the exact existing body, unchanged, no
  `code` field. GET/PUT.
- `TeacherNotFoundError` -> 404, `{"detail": "Teacher not found"}` --
  the same code-less-404 house style as every other "this does not
  exist" outcome, reusing the existing exception. PUT only.
- `UnknownReferenceError` -> 422, `{"code": "UNKNOWN_REFERENCE",
  "detail": "...", "reference_kind": "day"|"period", "reference_id":
  "..."}` -- reused verbatim from the existing Teaching-Assignment
  contract; its wording is fully generic, not Teaching-Assignment
  -specific, so this is genuine reuse, not a forced fit. PUT only.
- `InvalidTeacherAvailabilityError` -> 422, `{"code":
  "INVALID_TEACHER_AVAILABILITY", "detail": "...", "errors": [...]}`
  -- mirrors `InvalidTeacherErrorResponse` exactly. PUT only.
- `ConfigurationLockedError` -> 409, `{"code":
  "SCHEDULING_CONFIGURATION_LOCKED", "detail": "Scheduling
  configuration is locked because a schedule already exists"}` -- the
  stable Decision #35 HTTP contract, reused verbatim. PUT only.

Deliberately NOT caught here, by design: any other unexpected exception
-- an internal defect, never a client-actionable outcome, left to reach
FastAPI's normal unhandled-exception (generic 500) behavior, matching
every other route in this API.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from school_timetable.api.dependencies import (
    get_teacher_availability_service,
    get_teacher_availability_projection_service,
)
from school_timetable.api.schemas import (
    ConfigurationLockedErrorResponse,
    InvalidTeacherAvailabilityErrorResponse,
    TeacherAvailabilityProjectionResponse,
    TeacherAvailabilityReplaceRequest,
    TeacherAvailabilityWriteResponse,
    UnknownReferenceErrorResponse,
)
from school_timetable.api.serializer import (
    teacher_availability_projection_response_from_view,
    teacher_availability_write_response_from_result,
    validation_diagnostic_responses_from_warnings,
)
from school_timetable.application.errors import (
    ConfigurationLockedError,
    InvalidTeacherAvailabilityError,
    SchedulingProblemNotFoundError,
    TeacherNotFoundError,
    UnknownReferenceError,
)
from school_timetable.application.teacher_availability_models import (
    TeacherAvailabilityExceptionFields,
    TeacherAvailabilityReplaceFields,
)
from school_timetable.application.teacher_availability_projection_service import (
    TeacherAvailabilityProjectionService,
)
from school_timetable.application.teacher_availability_service import TeacherAvailabilityService

router = APIRouter()


@router.get(
    "/schools/{school_id}/years/{year_id}/teacher-availability",
    response_model=TeacherAvailabilityProjectionResponse,
)
def get_teacher_availability(
    school_id: str,
    year_id: str,
    service: TeacherAvailabilityProjectionService = Depends(get_teacher_availability_projection_service),
) -> TeacherAvailabilityProjectionResponse:
    try:
        view = service.project(school_id, year_id)
    except SchedulingProblemNotFoundError:
        raise HTTPException(status_code=404, detail="Scheduling configuration not found") from None
    return teacher_availability_projection_response_from_view(view)


@router.put(
    "/schools/{school_id}/years/{year_id}/teacher-availability/{teacher_id}",
    response_model=TeacherAvailabilityWriteResponse,
)
def replace_teacher_availability(
    school_id: str,
    year_id: str,
    teacher_id: str,
    body: TeacherAvailabilityReplaceRequest,
    service: TeacherAvailabilityService = Depends(get_teacher_availability_service),
):
    try:
        result = service.replace_exceptions(school_id, year_id, teacher_id, _fields_from_request(body))
    except SchedulingProblemNotFoundError:
        raise HTTPException(status_code=404, detail="Scheduling configuration not found") from None
    except TeacherNotFoundError:
        raise HTTPException(status_code=404, detail="Teacher not found") from None
    except _WRITE_ERRORS as exc:
        return _write_error_response(exc)
    return teacher_availability_write_response_from_result(result)


def _fields_from_request(body: TeacherAvailabilityReplaceRequest) -> TeacherAvailabilityReplaceFields:
    return TeacherAvailabilityReplaceFields(
        exceptions=tuple(
            TeacherAvailabilityExceptionFields(day_id=e.day_id, period_id=e.period_id, status=e.status)
            for e in body.exceptions
        ),
    )


_WRITE_ERRORS = (
    InvalidTeacherAvailabilityError,
    UnknownReferenceError,
    ConfigurationLockedError,
)


def _write_error_response(exc: Exception) -> JSONResponse:
    """One dispatch table from exception type to its locked
    `{status_code, body}` contract, never duplicated per route."""
    if isinstance(exc, InvalidTeacherAvailabilityError):
        return JSONResponse(
            status_code=422,
            content=InvalidTeacherAvailabilityErrorResponse(
                code="INVALID_TEACHER_AVAILABILITY",
                detail=str(exc),
                errors=validation_diagnostic_responses_from_warnings(exc.validation_errors),
            ).model_dump(),
        )
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
    # ConfigurationLockedError -- the last member of `_WRITE_ERRORS`.
    return JSONResponse(
        status_code=409,
        content=ConfigurationLockedErrorResponse(
            code="SCHEDULING_CONFIGURATION_LOCKED",
            detail="Scheduling configuration is locked because a schedule already exists",
        ).model_dump(),
    )
