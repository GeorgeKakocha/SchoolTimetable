"""`GET/POST /schools/{school_id}/years/{year_id}/teaching-assignments`
and `PUT/DELETE .../teaching-assignments/{requirement_id}` (Phase
3C.2b, `docs/DECISIONS.md` #34-#36's locked HTTP contract).

All four routes depend only on `application/` Protocols/services
(`TeachingAssignmentsProjectionService`, `TeachingAssignmentService`),
never on a concrete `persistence/` class -- the composition root wiring
those concrete, session-factory-backed adapters lives entirely in
`api/dependencies.py`, matching `schedule_routes.py`'s exact discipline.

`school_id`/`year_id` are natural/domain IDs, never surrogate ones,
matching every other route's existing convention.

POST/PUT never return the full page projection -- the caller must
re-fetch `GET .../teaching-assignments` after any mutation anyway
(workload totals, ordering, and lock state may all have changed); the
write response exists only to hand back the natural ID and any
non-blocking save-time warnings (Decision #34's save-time validation
boundary). DELETE returns 200, never 204, for the identical reason: a
delete can legitimately surface a non-blocking warning a bodyless
response would silently discard.

Error mapping (locked, no remaining owner decisions):
- `SchedulingProblemNotFoundError` -> 404, `{"detail": "Scheduling
  configuration not found"}` -- the exact existing body, unchanged, no
  `code` field. All four routes.
- `TeachingAssignmentNotFoundError` -> 404, `{"detail": "Teaching
  assignment not found"}` -- the same code-less-404 house style as
  every other "this does not exist" outcome. PUT/DELETE only.
- `UnknownReferenceError` -> 422, `{"code": "UNKNOWN_REFERENCE",
  "detail": "...", "reference_kind": "...", "reference_id": "..."}`.
  POST/PUT.
- `NonWholeClassTargetError` -> 422, `{"code": "NON_WHOLE_CLASS_TARGET",
  "detail": "...", "participant_group_id": "...", "actual_role": "..."}`.
  POST/PUT.
- `AdvancedRequirementNotEditableError` -> 409, `{"code":
  "ADVANCED_REQUIREMENT_NOT_EDITABLE", "detail": "...",
  "advanced_reasons": [...]}` -- a conflict with the target's own
  current state, not malformed input; `advanced_reasons` is repeated
  here (even though a prior GET already exposed it) to protect a stale
  client. PUT/DELETE.
- `DuplicateTeachingAssignmentError` -> 409, `{"code":
  "DUPLICATE_TEACHING_ASSIGNMENT", "detail": "...", "teacher_id": "...",
  "participant_group_id": "...", "activity_id": "..."}` -- mirrors
  `ScheduleAlreadyExistsError`'s existing 409 pattern exactly. POST/PUT.
- `InvalidTeachingAssignmentError` -> 422, `{"code":
  "INVALID_TEACHING_ASSIGNMENT", "detail": "...", "errors": [...]}` --
  mirrors `InvalidConfigurationResponse` exactly. POST/PUT/DELETE.
- `ConfigurationLockedError` -> 409, `{"code":
  "SCHEDULING_CONFIGURATION_LOCKED", "detail": "Scheduling
  configuration is locked because a schedule already exists"}` -- the
  stable Decision #35 HTTP contract. POST/PUT/DELETE.

Deliberately NOT caught here, by design: any other unexpected exception
-- an internal defect, never a client-actionable outcome, left to reach
FastAPI's normal unhandled-exception (generic 500) behavior, matching
every other route in this API.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from school_timetable.api.dependencies import (
    get_teaching_assignment_service,
    get_teaching_assignments_projection_service,
)
from school_timetable.api.schemas import (
    AdvancedRequirementNotEditableErrorResponse,
    ConfigurationLockedErrorResponse,
    DuplicateTeachingAssignmentErrorResponse,
    InvalidTeachingAssignmentErrorResponse,
    NonWholeClassTargetErrorResponse,
    TeachingAssignmentDeleteResponse,
    TeachingAssignmentsProjectionResponse,
    TeachingAssignmentWriteRequest,
    TeachingAssignmentWriteResponse,
    UnknownReferenceErrorResponse,
)
from school_timetable.api.serializer import (
    teaching_assignments_projection_response_from_view,
    validation_diagnostic_responses_from_warnings,
)
from school_timetable.application.errors import (
    AdvancedRequirementNotEditableError,
    ConfigurationLockedError,
    DuplicateTeachingAssignmentError,
    InvalidTeachingAssignmentError,
    NonWholeClassTargetError,
    SchedulingProblemNotFoundError,
    TeachingAssignmentNotFoundError,
    UnknownReferenceError,
)
from school_timetable.application.teaching_assignment_models import TeachingAssignmentFields
from school_timetable.application.teaching_assignment_service import TeachingAssignmentService
from school_timetable.application.teaching_assignments_projection_service import (
    TeachingAssignmentsProjectionService,
)

router = APIRouter()


@router.get(
    "/schools/{school_id}/years/{year_id}/teaching-assignments",
    response_model=TeachingAssignmentsProjectionResponse,
)
def get_teaching_assignments(
    school_id: str,
    year_id: str,
    service: TeachingAssignmentsProjectionService = Depends(get_teaching_assignments_projection_service),
) -> TeachingAssignmentsProjectionResponse:
    try:
        view = service.project(school_id, year_id)
    except SchedulingProblemNotFoundError:
        raise HTTPException(status_code=404, detail="Scheduling configuration not found") from None
    return teaching_assignments_projection_response_from_view(view)


@router.post(
    "/schools/{school_id}/years/{year_id}/teaching-assignments",
    response_model=TeachingAssignmentWriteResponse,
    status_code=201,
)
def create_teaching_assignment(
    school_id: str,
    year_id: str,
    body: TeachingAssignmentWriteRequest,
    service: TeachingAssignmentService = Depends(get_teaching_assignment_service),
):
    try:
        result = service.create(school_id, year_id, _fields_from_request(body))
    except SchedulingProblemNotFoundError:
        raise HTTPException(status_code=404, detail="Scheduling configuration not found") from None
    except _WRITE_ERRORS as exc:
        return _write_error_response(exc)
    return TeachingAssignmentWriteResponse(
        id=result.natural_id, warnings=validation_diagnostic_responses_from_warnings(result.warnings),
    )


@router.put(
    "/schools/{school_id}/years/{year_id}/teaching-assignments/{requirement_id}",
    response_model=TeachingAssignmentWriteResponse,
)
def update_teaching_assignment(
    school_id: str,
    year_id: str,
    requirement_id: str,
    body: TeachingAssignmentWriteRequest,
    service: TeachingAssignmentService = Depends(get_teaching_assignment_service),
):
    try:
        result = service.update(school_id, year_id, requirement_id, _fields_from_request(body))
    except SchedulingProblemNotFoundError:
        raise HTTPException(status_code=404, detail="Scheduling configuration not found") from None
    except TeachingAssignmentNotFoundError:
        raise HTTPException(status_code=404, detail="Teaching assignment not found") from None
    except _WRITE_ERRORS as exc:
        return _write_error_response(exc)
    return TeachingAssignmentWriteResponse(
        id=result.natural_id, warnings=validation_diagnostic_responses_from_warnings(result.warnings),
    )


@router.delete(
    "/schools/{school_id}/years/{year_id}/teaching-assignments/{requirement_id}",
    response_model=TeachingAssignmentDeleteResponse,
)
def delete_teaching_assignment(
    school_id: str,
    year_id: str,
    requirement_id: str,
    service: TeachingAssignmentService = Depends(get_teaching_assignment_service),
):
    try:
        warnings = service.delete(school_id, year_id, requirement_id)
    except SchedulingProblemNotFoundError:
        raise HTTPException(status_code=404, detail="Scheduling configuration not found") from None
    except TeachingAssignmentNotFoundError:
        raise HTTPException(status_code=404, detail="Teaching assignment not found") from None
    except _WRITE_ERRORS as exc:
        return _write_error_response(exc)
    return TeachingAssignmentDeleteResponse(
        deleted_id=requirement_id, warnings=validation_diagnostic_responses_from_warnings(warnings),
    )


def _fields_from_request(body: TeachingAssignmentWriteRequest) -> TeachingAssignmentFields:
    return TeachingAssignmentFields(
        teacher_id=body.teacher_id,
        participant_group_id=body.participant_group_id,
        activity_id=body.activity_id,
        weekly_periods=body.weekly_periods,
    )


_WRITE_ERRORS = (
    UnknownReferenceError,
    NonWholeClassTargetError,
    AdvancedRequirementNotEditableError,
    DuplicateTeachingAssignmentError,
    InvalidTeachingAssignmentError,
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
    if isinstance(exc, NonWholeClassTargetError):
        return JSONResponse(
            status_code=422,
            content=NonWholeClassTargetErrorResponse(
                code="NON_WHOLE_CLASS_TARGET",
                detail=str(exc),
                participant_group_id=exc.participant_group_id,
                actual_role=exc.actual_role,
            ).model_dump(),
        )
    if isinstance(exc, AdvancedRequirementNotEditableError):
        return JSONResponse(
            status_code=409,
            content=AdvancedRequirementNotEditableErrorResponse(
                code="ADVANCED_REQUIREMENT_NOT_EDITABLE",
                detail=str(exc),
                advanced_reasons=exc.reasons,
            ).model_dump(),
        )
    if isinstance(exc, DuplicateTeachingAssignmentError):
        return JSONResponse(
            status_code=409,
            content=DuplicateTeachingAssignmentErrorResponse(
                code="DUPLICATE_TEACHING_ASSIGNMENT",
                detail=str(exc),
                teacher_id=exc.teacher_id,
                participant_group_id=exc.participant_group_id,
                activity_id=exc.activity_id,
            ).model_dump(),
        )
    if isinstance(exc, InvalidTeachingAssignmentError):
        return JSONResponse(
            status_code=422,
            content=InvalidTeachingAssignmentErrorResponse(
                code="INVALID_TEACHING_ASSIGNMENT",
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
