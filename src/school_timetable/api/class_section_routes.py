"""`GET/POST /schools/{school_id}/years/{year_id}/classes` and
`PUT/DELETE .../classes/{class_id}` (Real-School Setup MVP Slice C).

All four routes depend only on `application/` Protocols/services
(`ClassSectionProjectionService`, `ClassSectionService`), never on a
concrete `persistence/` class -- the composition root wiring those
concrete, session-factory-backed adapters lives entirely in
`api/dependencies.py`, matching `teacher_routes.py`'s exact discipline.

`school_id`/`year_id` are natural/domain IDs, never surrogate ones,
matching every other route's existing convention.

POST/PUT return the full written `ClassSection`'s own resolved fields
directly (unlike Teaching Assignments' minimal `{id, warnings}`) -- a
Class write affects nothing but its own row and its internal, never-
exposed canonical `WHOLE_CLASS` group, so there is no larger page
projection to protect against staleness by withholding fields; the
caller must still re-fetch `GET .../classes` for page-level state
(`configuration_locked`, full ordered list). DELETE returns 200 with
`{"deleted_id": ...}`, no `warnings` field -- Class writes have no
TeachingAssignment-style quantitative warning mechanism.

Error mapping (locked, no remaining owner decisions -- see the Slice C
architecture gate):
- `SchedulingProblemNotFoundError` -> 404, `{"detail": "Scheduling
  configuration not found"}` -- the exact existing body, unchanged, no
  `code` field. All four routes.
- `ClassSectionNotFoundError` -> 404, `{"detail": "Class section not
  found"}` -- reuses the existing exception/wording already used by
  Class Timetable, verbatim. PUT/DELETE only.
- `InvalidClassError` -> 422, `{"code": "INVALID_CLASS", "detail":
  "...", "errors": [...]}` -- mirrors `InvalidTeacherError` exactly.
  POST/PUT.
- `DuplicateClassError` -> 409, `{"code": "DUPLICATE_CLASS", "detail":
  "..."}` -- mirrors `DuplicateTeachingAssignmentError`'s existing 409
  precedent (a conflict with existing state, not malformed input).
  POST/PUT.
- `ClassSectionInUseError` -> 409, `{"code": "CLASS_IN_USE", "detail":
  "...", "referenced_by": [...]}`. DELETE.
- `ConfigurationLockedError` -> 409, `{"code":
  "SCHEDULING_CONFIGURATION_LOCKED", "detail": "Scheduling
  configuration is locked because a schedule already exists"}` -- the
  stable Decision #35 HTTP contract, reused verbatim. POST/PUT/DELETE.

Deliberately NOT caught here, by design: any other unexpected exception
-- an internal defect, including a corrupted canonical `WHOLE_CLASS`
invariant (`class_section_rules.CanonicalWholeClassGroupInvariantError`)
-- never a client-actionable outcome, left to reach FastAPI's normal
unhandled-exception (generic 500) behavior, matching every other route
in this API. `ConfigurationChangedDuringGenerationError` is never
mapped here -- it belongs solely to generation's own final persist path.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from school_timetable.api.dependencies import get_class_section_service, get_classes_projection_service
from school_timetable.api.schemas import (
    ClassSectionDeleteResponse,
    ClassSectionInUseErrorResponse,
    ClassSectionsProjectionResponse,
    ClassSectionWriteRequest,
    ClassSectionWriteResponse,
    ConfigurationLockedErrorResponse,
    DuplicateClassErrorResponse,
    InvalidClassErrorResponse,
)
from school_timetable.api.serializer import (
    class_sections_projection_response_from_view,
    validation_diagnostic_responses_from_warnings,
)
from school_timetable.application.class_section_models import ClassSectionFields
from school_timetable.application.class_section_projection_service import ClassSectionProjectionService
from school_timetable.application.class_section_service import ClassSectionService
from school_timetable.application.errors import (
    ClassSectionInUseError,
    ClassSectionNotFoundError,
    ConfigurationLockedError,
    DuplicateClassError,
    InvalidClassError,
    SchedulingProblemNotFoundError,
)

router = APIRouter()


@router.get(
    "/schools/{school_id}/years/{year_id}/classes",
    response_model=ClassSectionsProjectionResponse,
)
def get_classes(
    school_id: str,
    year_id: str,
    service: ClassSectionProjectionService = Depends(get_classes_projection_service),
) -> ClassSectionsProjectionResponse:
    try:
        view = service.project(school_id, year_id)
    except SchedulingProblemNotFoundError:
        raise HTTPException(status_code=404, detail="Scheduling configuration not found") from None
    return class_sections_projection_response_from_view(view)


@router.post(
    "/schools/{school_id}/years/{year_id}/classes",
    response_model=ClassSectionWriteResponse,
    status_code=201,
)
def create_class(
    school_id: str,
    year_id: str,
    body: ClassSectionWriteRequest,
    service: ClassSectionService = Depends(get_class_section_service),
):
    try:
        result = service.create(school_id, year_id, _fields_from_request(body))
    except SchedulingProblemNotFoundError:
        raise HTTPException(status_code=404, detail="Scheduling configuration not found") from None
    except _WRITE_ERRORS as exc:
        return _write_error_response(exc)
    return ClassSectionWriteResponse(id=result.id, name=result.name)


@router.put(
    "/schools/{school_id}/years/{year_id}/classes/{class_id}",
    response_model=ClassSectionWriteResponse,
)
def update_class(
    school_id: str,
    year_id: str,
    class_id: str,
    body: ClassSectionWriteRequest,
    service: ClassSectionService = Depends(get_class_section_service),
):
    try:
        result = service.update(school_id, year_id, class_id, _fields_from_request(body))
    except SchedulingProblemNotFoundError:
        raise HTTPException(status_code=404, detail="Scheduling configuration not found") from None
    except ClassSectionNotFoundError:
        raise HTTPException(status_code=404, detail="Class section not found") from None
    except _WRITE_ERRORS as exc:
        return _write_error_response(exc)
    return ClassSectionWriteResponse(id=result.id, name=result.name)


@router.delete(
    "/schools/{school_id}/years/{year_id}/classes/{class_id}",
    response_model=ClassSectionDeleteResponse,
)
def delete_class(
    school_id: str,
    year_id: str,
    class_id: str,
    service: ClassSectionService = Depends(get_class_section_service),
):
    try:
        service.delete(school_id, year_id, class_id)
    except SchedulingProblemNotFoundError:
        raise HTTPException(status_code=404, detail="Scheduling configuration not found") from None
    except ClassSectionNotFoundError:
        raise HTTPException(status_code=404, detail="Class section not found") from None
    except _WRITE_ERRORS as exc:
        return _write_error_response(exc)
    return ClassSectionDeleteResponse(deleted_id=class_id)


def _fields_from_request(body: ClassSectionWriteRequest) -> ClassSectionFields:
    return ClassSectionFields(name=body.name)


_WRITE_ERRORS = (
    InvalidClassError,
    DuplicateClassError,
    ClassSectionInUseError,
    ConfigurationLockedError,
)


def _write_error_response(exc: Exception) -> JSONResponse:
    """Shared by create/update/delete -- one dispatch table from
    exception type to its locked `{status_code, body}` contract, never
    duplicated per route."""
    if isinstance(exc, InvalidClassError):
        return JSONResponse(
            status_code=422,
            content=InvalidClassErrorResponse(
                code="INVALID_CLASS",
                detail=str(exc),
                errors=validation_diagnostic_responses_from_warnings(exc.validation_errors),
            ).model_dump(),
        )
    if isinstance(exc, DuplicateClassError):
        return JSONResponse(
            status_code=409,
            content=DuplicateClassErrorResponse(code="DUPLICATE_CLASS", detail=str(exc)).model_dump(),
        )
    if isinstance(exc, ClassSectionInUseError):
        return JSONResponse(
            status_code=409,
            content=ClassSectionInUseErrorResponse(
                code="CLASS_IN_USE",
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
