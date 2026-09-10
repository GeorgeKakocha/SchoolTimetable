"""`GET/POST /schools/{school_id}/years/{year_id}/resources` and
`PUT/DELETE .../resources/{resource_id}` (Resources Slice A).

`Resource` is the existing `domain.resources.Resource(id, name,
capacity)` -- not a new/redesigned entity; this slice only adds the
missing catalog write surface over it. All four routes depend only on
`application/` Protocols/services (`ResourceProjectionService`,
`ResourceService`), never on a concrete `persistence/` class -- the
composition root wiring those concrete, session-factory-backed
adapters lives entirely in `api/dependencies.py`, matching
`special_activity_routes.py`'s exact discipline.

`school_id`/`year_id` are natural/domain IDs, never surrogate ones,
matching every other route's existing convention.

POST/PUT return the full written Resource's own resolved fields
directly. DELETE returns 200 with `{"deleted_id": ...}`, no `warnings`
field -- Resource writes have no TeachingAssignment-style quantitative
warning mechanism.

Error mapping (mirrors `special_activity_routes.py`'s exact locked
contract):
- `SchedulingProblemNotFoundError` -> 404, `{"detail": "Scheduling
  configuration not found"}` -- the exact existing body, unchanged, no
  `code` field. All four routes.
- `ResourceNotFoundError` -> 404, `{"detail": "Resource not found"}`.
  PUT/DELETE only.
- `InvalidResourceError` -> 422, `{"code": "INVALID_RESOURCE", "detail":
  "...", "errors": [...]}` -- mirrors `InvalidSpecialActivityError`
  exactly. POST/PUT.
- `DuplicateResourceError` -> 409, `{"code": "DUPLICATE_RESOURCE",
  "detail": "..."}` -- mirrors `DuplicateSpecialActivityError`'s
  existing 409 precedent (a conflict with existing state, not
  malformed input). POST/PUT.
- `ResourceInUseError` -> 409, `{"code": "RESOURCE_IN_USE", "detail":
  "...", "referenced_by": [...]}`. DELETE.
- `ConfigurationLockedError` -> 409, `{"code":
  "SCHEDULING_CONFIGURATION_LOCKED", "detail": "Scheduling
  configuration is locked because a schedule already exists"}` -- the
  stable Decision #35 HTTP contract, reused verbatim. POST/PUT/DELETE.

Deliberately NOT caught here, by design: any other unexpected exception
-- an internal defect, never a client-actionable outcome, left to reach
FastAPI's normal unhandled-exception (generic 500) behavior, matching
every other route in this API.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from school_timetable.api.dependencies import get_resource_service, get_resources_projection_service
from school_timetable.api.schemas import (
    ConfigurationLockedErrorResponse,
    DuplicateResourceErrorResponse,
    InvalidResourceErrorResponse,
    ResourceDeleteResponse,
    ResourceInUseErrorResponse,
    ResourcesProjectionResponse,
    ResourceWriteRequest,
    ResourceWriteResponse,
)
from school_timetable.api.serializer import (
    resources_projection_response_from_view,
    validation_diagnostic_responses_from_warnings,
)
from school_timetable.application.errors import (
    ConfigurationLockedError,
    DuplicateResourceError,
    InvalidResourceError,
    ResourceInUseError,
    ResourceNotFoundError,
    SchedulingProblemNotFoundError,
)
from school_timetable.application.resource_models import ResourceFields
from school_timetable.application.resource_projection_service import ResourceProjectionService
from school_timetable.application.resource_service import ResourceService

router = APIRouter()


@router.get(
    "/schools/{school_id}/years/{year_id}/resources",
    response_model=ResourcesProjectionResponse,
)
def get_resources(
    school_id: str,
    year_id: str,
    service: ResourceProjectionService = Depends(get_resources_projection_service),
) -> ResourcesProjectionResponse:
    try:
        view = service.project(school_id, year_id)
    except SchedulingProblemNotFoundError:
        raise HTTPException(status_code=404, detail="Scheduling configuration not found") from None
    return resources_projection_response_from_view(view)


@router.post(
    "/schools/{school_id}/years/{year_id}/resources",
    response_model=ResourceWriteResponse,
    status_code=201,
)
def create_resource(
    school_id: str,
    year_id: str,
    body: ResourceWriteRequest,
    service: ResourceService = Depends(get_resource_service),
):
    try:
        result = service.create(school_id, year_id, _fields_from_request(body))
    except SchedulingProblemNotFoundError:
        raise HTTPException(status_code=404, detail="Scheduling configuration not found") from None
    except _WRITE_ERRORS as exc:
        return _write_error_response(exc)
    return ResourceWriteResponse(id=result.id, name=result.name, capacity=result.capacity)


@router.put(
    "/schools/{school_id}/years/{year_id}/resources/{resource_id}",
    response_model=ResourceWriteResponse,
)
def update_resource(
    school_id: str,
    year_id: str,
    resource_id: str,
    body: ResourceWriteRequest,
    service: ResourceService = Depends(get_resource_service),
):
    try:
        result = service.update(school_id, year_id, resource_id, _fields_from_request(body))
    except SchedulingProblemNotFoundError:
        raise HTTPException(status_code=404, detail="Scheduling configuration not found") from None
    except ResourceNotFoundError:
        raise HTTPException(status_code=404, detail="Resource not found") from None
    except _WRITE_ERRORS as exc:
        return _write_error_response(exc)
    return ResourceWriteResponse(id=result.id, name=result.name, capacity=result.capacity)


@router.delete(
    "/schools/{school_id}/years/{year_id}/resources/{resource_id}",
    response_model=ResourceDeleteResponse,
)
def delete_resource(
    school_id: str,
    year_id: str,
    resource_id: str,
    service: ResourceService = Depends(get_resource_service),
):
    try:
        service.delete(school_id, year_id, resource_id)
    except SchedulingProblemNotFoundError:
        raise HTTPException(status_code=404, detail="Scheduling configuration not found") from None
    except ResourceNotFoundError:
        raise HTTPException(status_code=404, detail="Resource not found") from None
    except _WRITE_ERRORS as exc:
        return _write_error_response(exc)
    return ResourceDeleteResponse(deleted_id=resource_id)


def _fields_from_request(body: ResourceWriteRequest) -> ResourceFields:
    return ResourceFields(name=body.name, capacity=body.capacity)


_WRITE_ERRORS = (
    InvalidResourceError,
    DuplicateResourceError,
    ResourceInUseError,
    ConfigurationLockedError,
)


def _write_error_response(exc: Exception) -> JSONResponse:
    """Shared by create/update/delete -- one dispatch table from
    exception type to its locked `{status_code, body}` contract, never
    duplicated per route."""
    if isinstance(exc, InvalidResourceError):
        return JSONResponse(
            status_code=422,
            content=InvalidResourceErrorResponse(
                code="INVALID_RESOURCE",
                detail=str(exc),
                errors=validation_diagnostic_responses_from_warnings(exc.validation_errors),
            ).model_dump(),
        )
    if isinstance(exc, DuplicateResourceError):
        return JSONResponse(
            status_code=409,
            content=DuplicateResourceErrorResponse(
                code="DUPLICATE_RESOURCE", detail=str(exc),
            ).model_dump(),
        )
    if isinstance(exc, ResourceInUseError):
        return JSONResponse(
            status_code=409,
            content=ResourceInUseErrorResponse(
                code="RESOURCE_IN_USE",
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
