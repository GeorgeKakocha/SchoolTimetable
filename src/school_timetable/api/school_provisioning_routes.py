"""HTTP boundary for atomic School/initial-year provisioning."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from school_timetable.api.dependencies import get_school_provisioning_service
from school_timetable.api.schemas import (
    AcademicYearProvisioningConflictErrorResponse,
    InvalidSchoolProvisioningErrorResponse,
    ProvisionSchoolRequest,
    ProvisionSchoolResponse,
    SchoolProvisioningConflictErrorResponse,
)
from school_timetable.api.serializer import (
    provision_school_response_from_result,
    validation_diagnostic_responses_from_warnings,
)
from school_timetable.application.errors import (
    AcademicYearProvisioningConflictError,
    InvalidSchoolProvisioningError,
    SchoolProvisioningConflictError,
)
from school_timetable.application.school_provisioning_models import ProvisionSchoolWithInitialYearCommand
from school_timetable.application.school_provisioning_service import ProvisionSchoolWithInitialYearService

router = APIRouter()


@router.post("/schools", response_model=ProvisionSchoolResponse, status_code=201)
def provision_school(
    body: ProvisionSchoolRequest,
    service: ProvisionSchoolWithInitialYearService = Depends(get_school_provisioning_service),
):
    try:
        result = service.provision(ProvisionSchoolWithInitialYearCommand(
            school_id=body.school_id,
            school_name=body.school_name,
            academic_year_id=body.initial_academic_year.academic_year_id,
            academic_year_label=body.initial_academic_year.label,
        ))
    except InvalidSchoolProvisioningError as exc:
        return JSONResponse(
            status_code=422,
            content=InvalidSchoolProvisioningErrorResponse(
                code="INVALID_SCHOOL_PROVISIONING",
                detail=str(exc),
                errors=validation_diagnostic_responses_from_warnings(exc.validation_errors),
            ).model_dump(),
        )
    except SchoolProvisioningConflictError as exc:
        return JSONResponse(
            status_code=409,
            content=SchoolProvisioningConflictErrorResponse(
                code="SCHOOL_ID_ALREADY_EXISTS", detail=str(exc),
            ).model_dump(),
        )
    except AcademicYearProvisioningConflictError as exc:
        return JSONResponse(
            status_code=409,
            content=AcademicYearProvisioningConflictErrorResponse(
                code="ACADEMIC_YEAR_ID_ALREADY_EXISTS", detail=str(exc),
            ).model_dump(),
        )
    return provision_school_response_from_result(result)
