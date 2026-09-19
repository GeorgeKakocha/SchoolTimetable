"""Application orchestration for atomic School/initial-year provisioning."""
from __future__ import annotations

from school_timetable.application.errors import InvalidSchoolProvisioningError
from school_timetable.application.ports import SchoolProvisioningRepository
from school_timetable.application.school_provisioning_models import (
    ProvisionSchoolWithInitialYearCommand,
    ProvisionSchoolWithInitialYearResult,
)
from school_timetable.validation.errors import ValidationError


class ProvisionSchoolWithInitialYearService:
    def __init__(self, repository: SchoolProvisioningRepository) -> None:
        self._repository = repository

    def provision(
        self, command: ProvisionSchoolWithInitialYearCommand,
    ) -> ProvisionSchoolWithInitialYearResult:
        normalized = ProvisionSchoolWithInitialYearCommand(
            school_id=command.school_id.strip(),
            school_name=command.school_name.strip(),
            academic_year_id=command.academic_year_id.strip(),
            academic_year_label=command.academic_year_label.strip(),
        )
        errors: list[ValidationError] = []
        for field_name, value in (
            ("school_id", normalized.school_id),
            ("school_name", normalized.school_name),
            ("academic_year_id", normalized.academic_year_id),
            ("academic_year_label", normalized.academic_year_label),
        ):
            if value == "":
                errors.append(ValidationError(
                    f"BLANK_{field_name.upper()}",
                    f"{field_name} is blank after trimming",
                ))
        if errors:
            raise InvalidSchoolProvisioningError(tuple(errors))
        return self._repository.provision_school_with_initial_year(normalized)
