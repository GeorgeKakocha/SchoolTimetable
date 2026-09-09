"""`GET/POST /schools/{school_id}/years/{year_id}/subjects` and
`PUT/DELETE .../subjects/{subject_id}` (Real-School Setup MVP Slice D).

"Subject" is not a new domain entity -- it is the user-facing name for
`Activity(kind=ORDINARY)` (see `domain/activities.py`). All four routes
depend only on `application/` Protocols/services
(`SubjectProjectionService`, `SubjectService`), never on a concrete
`persistence/` class -- the composition root wiring those concrete,
session-factory-backed adapters lives entirely in `api/dependencies.py`,
matching `class_section_routes.py`'s exact discipline.

`school_id`/`year_id` are natural/domain IDs, never surrogate ones,
matching every other route's existing convention.

POST/PUT return the full written Subject's own resolved fields directly
(unlike Teaching Assignments' minimal `{id, warnings}`) -- a Subject
write affects nothing but its own row, so there is no larger page
projection to protect against staleness by withholding fields; the
caller must still re-fetch `GET .../subjects` for page-level state
(`configuration_locked`, full ordered list). DELETE returns 200 with
`{"deleted_id": ...}`, no `warnings` field -- Subject writes have no
TeachingAssignment-style quantitative warning mechanism.

Error mapping (locked, no remaining owner decisions -- see the Slice D
architecture gate):
- `SchedulingProblemNotFoundError` -> 404, `{"detail": "Scheduling
  configuration not found"}` -- the exact existing body, unchanged, no
  `code` field. All four routes.
- `SubjectNotFoundError` -> 404, `{"detail": "Subject not found"}` --
  raised identically whether `subject_id` does not exist at all, or it
  exists but is `ActivityKind.CLUB`; a CLUB activity is never
  distinguishable from a missing Subject through this filtered
  resource surface. PUT/DELETE only.
- `InvalidSubjectError` -> 422, `{"code": "INVALID_SUBJECT", "detail":
  "...", "errors": [...]}` -- mirrors `InvalidClassError` exactly.
  POST/PUT.
- `DuplicateSubjectError` -> 409, `{"code": "DUPLICATE_SUBJECT",
  "detail": "..."}` -- mirrors `DuplicateClassError`'s existing 409
  precedent (a conflict with existing state, not malformed input).
  POST/PUT.
- `SubjectInUseError` -> 409, `{"code": "SUBJECT_IN_USE", "detail":
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
persist path.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from school_timetable.api.dependencies import get_subject_service, get_subjects_projection_service
from school_timetable.api.schemas import (
    ConfigurationLockedErrorResponse,
    DuplicateSubjectErrorResponse,
    InvalidSubjectErrorResponse,
    SubjectDeleteResponse,
    SubjectInUseErrorResponse,
    SubjectsProjectionResponse,
    SubjectWriteRequest,
    SubjectWriteResponse,
)
from school_timetable.api.serializer import (
    subjects_projection_response_from_view,
    validation_diagnostic_responses_from_warnings,
)
from school_timetable.application.errors import (
    ConfigurationLockedError,
    DuplicateSubjectError,
    InvalidSubjectError,
    SchedulingProblemNotFoundError,
    SubjectInUseError,
    SubjectNotFoundError,
)
from school_timetable.application.subject_models import SubjectFields
from school_timetable.application.subject_projection_service import SubjectProjectionService
from school_timetable.application.subject_service import SubjectService

router = APIRouter()


@router.get(
    "/schools/{school_id}/years/{year_id}/subjects",
    response_model=SubjectsProjectionResponse,
)
def get_subjects(
    school_id: str,
    year_id: str,
    service: SubjectProjectionService = Depends(get_subjects_projection_service),
) -> SubjectsProjectionResponse:
    try:
        view = service.project(school_id, year_id)
    except SchedulingProblemNotFoundError:
        raise HTTPException(status_code=404, detail="Scheduling configuration not found") from None
    return subjects_projection_response_from_view(view)


@router.post(
    "/schools/{school_id}/years/{year_id}/subjects",
    response_model=SubjectWriteResponse,
    status_code=201,
)
def create_subject(
    school_id: str,
    year_id: str,
    body: SubjectWriteRequest,
    service: SubjectService = Depends(get_subject_service),
):
    try:
        result = service.create(school_id, year_id, _fields_from_request(body))
    except SchedulingProblemNotFoundError:
        raise HTTPException(status_code=404, detail="Scheduling configuration not found") from None
    except _WRITE_ERRORS as exc:
        return _write_error_response(exc)
    return SubjectWriteResponse(id=result.id, name=result.name)


@router.put(
    "/schools/{school_id}/years/{year_id}/subjects/{subject_id}",
    response_model=SubjectWriteResponse,
)
def update_subject(
    school_id: str,
    year_id: str,
    subject_id: str,
    body: SubjectWriteRequest,
    service: SubjectService = Depends(get_subject_service),
):
    try:
        result = service.update(school_id, year_id, subject_id, _fields_from_request(body))
    except SchedulingProblemNotFoundError:
        raise HTTPException(status_code=404, detail="Scheduling configuration not found") from None
    except SubjectNotFoundError:
        raise HTTPException(status_code=404, detail="Subject not found") from None
    except _WRITE_ERRORS as exc:
        return _write_error_response(exc)
    return SubjectWriteResponse(id=result.id, name=result.name)


@router.delete(
    "/schools/{school_id}/years/{year_id}/subjects/{subject_id}",
    response_model=SubjectDeleteResponse,
)
def delete_subject(
    school_id: str,
    year_id: str,
    subject_id: str,
    service: SubjectService = Depends(get_subject_service),
):
    try:
        service.delete(school_id, year_id, subject_id)
    except SchedulingProblemNotFoundError:
        raise HTTPException(status_code=404, detail="Scheduling configuration not found") from None
    except SubjectNotFoundError:
        raise HTTPException(status_code=404, detail="Subject not found") from None
    except _WRITE_ERRORS as exc:
        return _write_error_response(exc)
    return SubjectDeleteResponse(deleted_id=subject_id)


def _fields_from_request(body: SubjectWriteRequest) -> SubjectFields:
    return SubjectFields(name=body.name)


_WRITE_ERRORS = (
    InvalidSubjectError,
    DuplicateSubjectError,
    SubjectInUseError,
    ConfigurationLockedError,
)


def _write_error_response(exc: Exception) -> JSONResponse:
    """Shared by create/update/delete -- one dispatch table from
    exception type to its locked `{status_code, body}` contract, never
    duplicated per route."""
    if isinstance(exc, InvalidSubjectError):
        return JSONResponse(
            status_code=422,
            content=InvalidSubjectErrorResponse(
                code="INVALID_SUBJECT",
                detail=str(exc),
                errors=validation_diagnostic_responses_from_warnings(exc.validation_errors),
            ).model_dump(),
        )
    if isinstance(exc, DuplicateSubjectError):
        return JSONResponse(
            status_code=409,
            content=DuplicateSubjectErrorResponse(code="DUPLICATE_SUBJECT", detail=str(exc)).model_dump(),
        )
    if isinstance(exc, SubjectInUseError):
        return JSONResponse(
            status_code=409,
            content=SubjectInUseErrorResponse(
                code="SUBJECT_IN_USE",
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
