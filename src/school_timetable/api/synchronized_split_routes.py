"""HTTP create boundary for one atomic synchronized two-branch split."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from school_timetable.api.dependencies import get_synchronized_split_service
from school_timetable.api.schemas import (
    ConfigurationLockedErrorResponse,
    DuplicateSubgroupNamesErrorResponse,
    InvalidSynchronizedSplitErrorResponse,
    NonOrdinaryActivityTargetErrorResponse,
    SameTeacherSynchronizedSplitErrorResponse,
    SynchronizedSplitBranchResponse,
    SynchronizedSplitCreateRequest,
    SynchronizedSplitCreateResponse,
    SynchronizedSplitPublicIdCollisionErrorResponse,
    UnknownReferenceErrorResponse,
)
from school_timetable.api.serializer import validation_diagnostic_responses_from_warnings
from school_timetable.application.errors import (
    ClassSectionNotFoundError,
    ConfigurationLockedError,
    DuplicateSubgroupNameError,
    InvalidSynchronizedSplitError,
    NonOrdinaryActivityTargetError,
    PublicIdCollisionError,
    SameTeacherSynchronizedSplitError,
    SchedulingProblemNotFoundError,
    UnknownReferenceError,
)
from school_timetable.application.synchronized_split_models import (
    CreateSynchronizedSplitCommand,
    SynchronizedSplitBranchFields,
)
from school_timetable.application.synchronized_split_service import SynchronizedSplitService

router = APIRouter()


@router.post(
    "/schools/{school_id}/years/{year_id}/configuration/synchronized-splits",
    response_model=SynchronizedSplitCreateResponse,
    status_code=201,
)
def create_synchronized_split(
    school_id: str,
    year_id: str,
    body: SynchronizedSplitCreateRequest,
    service: SynchronizedSplitService = Depends(get_synchronized_split_service),
):
    command = CreateSynchronizedSplitCommand(
        school_id=school_id,
        academic_year_id=year_id,
        class_section_id=body.class_section_id,
        weekly_periods=body.weekly_periods,
        branch_a=_branch_fields(body.branches[0]),
        branch_b=_branch_fields(body.branches[1]),
    )
    try:
        result = service.create(command)
    except SchedulingProblemNotFoundError:
        raise HTTPException(status_code=404, detail="Scheduling configuration not found") from None
    except ClassSectionNotFoundError:
        raise HTTPException(status_code=404, detail="Class section not found") from None
    except _WRITE_ERRORS as exc:
        return _write_error_response(exc)

    return SynchronizedSplitCreateResponse(
        split_group_id=result.split_group_id,
        class_section_id=result.class_section_id,
        weekly_periods=result.weekly_periods,
        branches=(
            SynchronizedSplitBranchResponse(**vars(result.branch_a)),
            SynchronizedSplitBranchResponse(**vars(result.branch_b)),
        ),
        warnings=validation_diagnostic_responses_from_warnings(result.warnings),
    )


def _branch_fields(branch) -> SynchronizedSplitBranchFields:
    return SynchronizedSplitBranchFields(
        participant_group_name=branch.participant_group_name,
        teacher_id=branch.teacher_id,
        activity_id=branch.activity_id,
    )


_WRITE_ERRORS = (
    UnknownReferenceError,
    NonOrdinaryActivityTargetError,
    DuplicateSubgroupNameError,
    SameTeacherSynchronizedSplitError,
    InvalidSynchronizedSplitError,
    PublicIdCollisionError,
    ConfigurationLockedError,
)


def _write_error_response(exc: Exception) -> JSONResponse:
    if isinstance(exc, UnknownReferenceError):
        return JSONResponse(status_code=422, content=UnknownReferenceErrorResponse(
            code="UNKNOWN_REFERENCE", detail=str(exc),
            reference_kind=exc.reference_kind, reference_id=exc.reference_id,
        ).model_dump())
    if isinstance(exc, NonOrdinaryActivityTargetError):
        return JSONResponse(status_code=422, content=NonOrdinaryActivityTargetErrorResponse(
            code="NON_ORDINARY_ACTIVITY_TARGET", detail=str(exc),
            activity_id=exc.activity_id, actual_kind=exc.actual_kind,
        ).model_dump())
    if isinstance(exc, DuplicateSubgroupNameError):
        return JSONResponse(status_code=422, content=DuplicateSubgroupNamesErrorResponse(
            code="DUPLICATE_SUBGROUP_NAMES", detail=str(exc),
        ).model_dump())
    if isinstance(exc, SameTeacherSynchronizedSplitError):
        return JSONResponse(status_code=422, content=SameTeacherSynchronizedSplitErrorResponse(
            code="SAME_TEACHER_SYNCHRONIZED_SPLIT", detail=str(exc), teacher_id=exc.teacher_id,
        ).model_dump())
    if isinstance(exc, InvalidSynchronizedSplitError):
        return JSONResponse(status_code=422, content=InvalidSynchronizedSplitErrorResponse(
            code="INVALID_SYNCHRONIZED_SPLIT", detail=str(exc),
            errors=validation_diagnostic_responses_from_warnings(exc.validation_errors),
        ).model_dump())
    if isinstance(exc, PublicIdCollisionError):
        return JSONResponse(status_code=409, content=SynchronizedSplitPublicIdCollisionErrorResponse(
            code="SYNCHRONIZED_SPLIT_PUBLIC_ID_COLLISION", detail=str(exc),
        ).model_dump())
    return JSONResponse(status_code=409, content=ConfigurationLockedErrorResponse(
        code="SCHEDULING_CONFIGURATION_LOCKED",
        detail="Scheduling configuration is locked because a schedule already exists",
    ).model_dump())
