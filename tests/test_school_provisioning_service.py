"""DB-free tests for the School/initial-year provisioning use case."""
from __future__ import annotations

from dataclasses import fields

import pytest

from school_timetable.application.configuration_revision_models import ConfigurationRevisionState
from school_timetable.application.errors import (
    AcademicYearProvisioningConflictError,
    InvalidSchoolProvisioningError,
    SchoolProvisioningConflictError,
)
from school_timetable.application.school_provisioning_models import (
    ProvisionedAcademicYear,
    ProvisionedSchool,
    ProvisionSchoolWithInitialYearCommand,
    ProvisionSchoolWithInitialYearResult,
)
from school_timetable.application.school_provisioning_service import ProvisionSchoolWithInitialYearService


def _command(**overrides) -> ProvisionSchoolWithInitialYearCommand:
    values = {
        "school_id": "real-school",
        "school_name": "Real School",
        "academic_year_id": "ay-2026-2027",
        "academic_year_label": "2026/2027",
    }
    values.update(overrides)
    return ProvisionSchoolWithInitialYearCommand(**values)


def _result(command: ProvisionSchoolWithInitialYearCommand) -> ProvisionSchoolWithInitialYearResult:
    return ProvisionSchoolWithInitialYearResult(
        school=ProvisionedSchool(command.school_id, command.school_name),
        academic_year=ProvisionedAcademicYear(command.academic_year_id, command.academic_year_label),
        configuration_state=ConfigurationRevisionState(None, 1, False, False, False),
    )


class _FakeRepository:
    def __init__(self) -> None:
        self.aggregates: dict[str, ProvisionSchoolWithInitialYearCommand] = {}
        self.calls: list[ProvisionSchoolWithInitialYearCommand] = []

    def provision_school_with_initial_year(self, command):
        self.calls.append(command)
        existing = self.aggregates.get(command.school_id)
        if existing is None:
            self.aggregates[command.school_id] = command
            return _result(command)
        if existing.school_name != command.school_name:
            raise SchoolProvisioningConflictError(command.school_id)
        if existing.academic_year_id != command.academic_year_id:
            raise SchoolProvisioningConflictError(command.school_id)
        if existing.academic_year_label != command.academic_year_label:
            raise AcademicYearProvisioningConflictError(command.school_id, command.academic_year_id)
        return _result(existing)


def test_success_returns_public_identity_and_initial_editable_state():
    repository = _FakeRepository()
    result = ProvisionSchoolWithInitialYearService(repository).provision(_command())
    assert result.school == ProvisionedSchool("real-school", "Real School")
    assert result.academic_year == ProvisionedAcademicYear("ay-2026-2027", "2026/2027")
    assert result.configuration_state == ConfigurationRevisionState(None, 1, False, False, False)


def test_result_contract_contains_no_surrogate_identity_fields():
    result = ProvisionSchoolWithInitialYearService(_FakeRepository()).provision(_command())
    assert {field.name for field in fields(result)} == {"school", "academic_year", "configuration_state"}
    assert {field.name for field in fields(result.school)} == {"id", "name"}
    assert {field.name for field in fields(result.academic_year)} == {"id", "label"}
    assert "configuration_revision_id" not in repr(result)


def test_exact_replay_is_idempotent():
    repository = _FakeRepository()
    service = ProvisionSchoolWithInitialYearService(repository)
    first = service.provision(_command())
    replay = service.provision(_command())
    assert replay == first
    assert repository.aggregates == {"real-school": _command()}


def test_conflicting_school_public_id_is_rejected():
    service = ProvisionSchoolWithInitialYearService(_FakeRepository())
    service.provision(_command())
    with pytest.raises(SchoolProvisioningConflictError):
        service.provision(_command(school_name="Another School"))


def test_conflicting_academic_year_identity_is_rejected():
    service = ProvisionSchoolWithInitialYearService(_FakeRepository())
    service.provision(_command())
    with pytest.raises(AcademicYearProvisioningConflictError):
        service.provision(_command(academic_year_label="Different Label"))


def test_same_year_public_id_under_another_school_is_a_distinct_aggregate():
    repository = _FakeRepository()
    service = ProvisionSchoolWithInitialYearService(repository)
    first = service.provision(_command())
    second = service.provision(_command(school_id="other-school", school_name="Other School"))
    assert first.school.id == "real-school"
    assert second.school.id == "other-school"
    assert first.academic_year.id == second.academic_year.id == "ay-2026-2027"
    assert len(repository.aggregates) == 2


def test_service_normalizes_fields_and_rejects_blank_input_without_http_concepts():
    repository = _FakeRepository()
    service = ProvisionSchoolWithInitialYearService(repository)
    result = service.provision(_command(school_name="  Real School  "))
    assert result.school.name == "Real School"
    with pytest.raises(InvalidSchoolProvisioningError) as exc_info:
        service.provision(_command(academic_year_id="   "))
    assert [error.code for error in exc_info.value.validation_errors] == ["BLANK_ACADEMIC_YEAR_ID"]
    assert not hasattr(exc_info.value, "status_code")
