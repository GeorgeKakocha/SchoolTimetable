"""`GET/POST /schools/{school_id}/years/{year_id}/teachers` and
`PUT/DELETE .../teachers/{teacher_id}` (Real-School Setup MVP Slice B).

All four routes depend only on `application/` Protocols/services
(`TeacherProjectionService`, `TeacherService`), never on a concrete
`persistence/` class -- the composition root wiring those concrete,
session-factory-backed adapters lives entirely in `api/dependencies.py`,
matching `teaching_assignment_routes.py`'s exact discipline.

`school_id`/`year_id` are natural/domain IDs, never surrogate ones,
matching every other route's existing convention.

POST/PUT return the full written `Teacher`'s own resolved fields
directly (unlike Teaching Assignments' minimal `{id, warnings}`) --
a Teacher write affects nothing but its own row, so there is no larger
page projection to protect against staleness by withholding fields; the
caller must still re-fetch `GET .../teachers` for page-level state
(`configuration_locked`, full ordered list). DELETE returns 200 with
`{"deleted_id": ...}`, no `warnings` field -- Teacher writes have no
TeachingAssignment-style quantitative warning mechanism.

Error mapping (locked, no remaining owner decisions -- see the Slice B
architecture gate):
- `SchedulingProblemNotFoundError` -> 404, `{"detail": "Scheduling
  configuration not found"}` -- the exact existing body, unchanged, no
  `code` field. All four routes.
- `TeacherNotFoundError` -> 404, `{"detail": "Teacher not found"}` --
  the same code-less-404 house style as every other "this does not
  exist" outcome, reusing the existing exception already used by
  Teacher Timetable. PUT/DELETE only.
- `InvalidTeacherError` -> 422, `{"code": "INVALID_TEACHER", "detail":
  "...", "errors": [...]}` -- mirrors `InvalidTeachingAssignmentError`
  exactly. POST/PUT.
- `TeacherInUseError` -> 409, `{"code": "TEACHER_IN_USE", "detail":
  "...", "referenced_by": [...]}`. DELETE.
- `ConfigurationLockedError` -> 409, `{"code":
  "SCHEDULING_CONFIGURATION_LOCKED", "detail": "Scheduling
  configuration is locked because a schedule already exists"}` -- the
  stable Decision #35 HTTP contract, reused verbatim. POST/PUT/DELETE.

Deliberately NOT caught here, by design: any other unexpected exception
-- an internal defect, never a client-actionable outcome, left to reach
FastAPI's normal unhandled-exception (generic 500) behavior, matching
every other route in this API. `ConfigurationChangedDuringGenerationError`
is never mapped here -- it belongs solely to generation's own final
persist path (`GenerateScheduleService`), never a Teacher write.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from school_timetable.api.dependencies import get_teacher_service, get_teachers_projection_service
from school_timetable.api.schemas import (
    ConfigurationLockedErrorResponse,
    InvalidTeacherErrorResponse,
    TeacherDeleteResponse,
    TeacherInUseErrorResponse,
    TeachersProjectionResponse,
    TeacherWriteRequest,
    TeacherWriteResponse,
)
from school_timetable.api.serializer import (
    teachers_projection_response_from_view,
    validation_diagnostic_responses_from_warnings,
)
from school_timetable.application.errors import (
    ConfigurationLockedError,
    InvalidTeacherError,
    SchedulingProblemNotFoundError,
    TeacherInUseError,
    TeacherNotFoundError,
)
from school_timetable.application.teacher_models import TeacherFields
from school_timetable.application.teacher_projection_service import TeacherProjectionService
from school_timetable.application.teacher_service import TeacherService

router = APIRouter()


@router.get(
    "/schools/{school_id}/years/{year_id}/teachers",
    response_model=TeachersProjectionResponse,
)
def get_teachers(
    school_id: str,
    year_id: str,
    service: TeacherProjectionService = Depends(get_teachers_projection_service),
) -> TeachersProjectionResponse:
    try:
        view = service.project(school_id, year_id)
    except SchedulingProblemNotFoundError:
        raise HTTPException(status_code=404, detail="Scheduling configuration not found") from None
    return teachers_projection_response_from_view(view)


@router.post(
    "/schools/{school_id}/years/{year_id}/teachers",
    response_model=TeacherWriteResponse,
    status_code=201,
)
def create_teacher(
    school_id: str,
    year_id: str,
    body: TeacherWriteRequest,
    service: TeacherService = Depends(get_teacher_service),
):
    try:
        result = service.create(school_id, year_id, _fields_from_request(body))
    except SchedulingProblemNotFoundError:
        raise HTTPException(status_code=404, detail="Scheduling configuration not found") from None
    except _WRITE_ERRORS as exc:
        return _write_error_response(exc)
    return TeacherWriteResponse(
        id=result.id, first_name=result.first_name, last_name=result.last_name, name=result.name,
    )


@router.put(
    "/schools/{school_id}/years/{year_id}/teachers/{teacher_id}",
    response_model=TeacherWriteResponse,
)
def update_teacher(
    school_id: str,
    year_id: str,
    teacher_id: str,
    body: TeacherWriteRequest,
    service: TeacherService = Depends(get_teacher_service),
):
    try:
        result = service.update(school_id, year_id, teacher_id, _fields_from_request(body))
    except SchedulingProblemNotFoundError:
        raise HTTPException(status_code=404, detail="Scheduling configuration not found") from None
    except TeacherNotFoundError:
        raise HTTPException(status_code=404, detail="Teacher not found") from None
    except _WRITE_ERRORS as exc:
        return _write_error_response(exc)
    return TeacherWriteResponse(
        id=result.id, first_name=result.first_name, last_name=result.last_name, name=result.name,
    )


@router.delete(
    "/schools/{school_id}/years/{year_id}/teachers/{teacher_id}",
    response_model=TeacherDeleteResponse,
)
def delete_teacher(
    school_id: str,
    year_id: str,
    teacher_id: str,
    service: TeacherService = Depends(get_teacher_service),
):
    try:
        service.delete(school_id, year_id, teacher_id)
    except SchedulingProblemNotFoundError:
        raise HTTPException(status_code=404, detail="Scheduling configuration not found") from None
    except TeacherNotFoundError:
        raise HTTPException(status_code=404, detail="Teacher not found") from None
    except _WRITE_ERRORS as exc:
        return _write_error_response(exc)
    return TeacherDeleteResponse(deleted_id=teacher_id)


def _fields_from_request(body: TeacherWriteRequest) -> TeacherFields:
    return TeacherFields(first_name=body.first_name, last_name=body.last_name)


_WRITE_ERRORS = (
    InvalidTeacherError,
    TeacherInUseError,
    ConfigurationLockedError,
)


def _write_error_response(exc: Exception) -> JSONResponse:
    """Shared by create/update/delete -- one dispatch table from
    exception type to its locked `{status_code, body}` contract, never
    duplicated per route."""
    if isinstance(exc, InvalidTeacherError):
        return JSONResponse(
            status_code=422,
            content=InvalidTeacherErrorResponse(
                code="INVALID_TEACHER",
                detail=str(exc),
                errors=validation_diagnostic_responses_from_warnings(exc.validation_errors),
            ).model_dump(),
        )
    if isinstance(exc, TeacherInUseError):
        return JSONResponse(
            status_code=409,
            content=TeacherInUseErrorResponse(
                code="TEACHER_IN_USE",
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
