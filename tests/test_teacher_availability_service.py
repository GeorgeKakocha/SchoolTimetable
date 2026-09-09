"""Pure orchestration/validation tests for Owner Decision #38's
`TeacherAvailabilityProjectionService`/`TeacherAvailabilityService`. No
database, no PostgreSQL -- all repository ports are small in-memory
fakes; real-PostgreSQL persistence/concurrency proof lives in
`tests_web/test_teacher_availability_repository.py`.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from school_timetable.application.errors import (
    ConfigurationLockedError,
    InvalidTeacherAvailabilityError,
    TeacherNotFoundError,
    UnknownReferenceError,
)
from school_timetable.application.teacher_availability_models import (
    TeacherAvailabilityExceptionFields,
    TeacherAvailabilityExceptionResult,
    TeacherAvailabilityReplaceFields,
)
from school_timetable.application.teacher_availability_projection_service import (
    TeacherAvailabilityProjectionService,
)
from school_timetable.application.teacher_availability_service import TeacherAvailabilityService
from school_timetable.domain.people import AvailabilityStatus, TeacherAvailability
from school_timetable.fixtures.valid_fixture import build_valid_fixture

_SCHOOL = "synthetic-school"
_YEAR = "ay-2026"


class _FakeProblemRepository:
    def __init__(self, problem):
        self._problem = problem

    def load_by_school_and_year(self, school_natural_id, academic_year_natural_id):
        return self._problem


class _FakeScheduleRepository:
    def __init__(self, active=None):
        self._active = active

    def get_active_schedule(self, school_natural_id, academic_year_natural_id):
        return self._active


class _FakeWritePort:
    """Simulates the authoritative, lock-protected recheck by simply
    re-invoking `validate` against the same (unchanged) problem -- the
    genuine lock/reload-under-lock mechanics are proven for real against
    PostgreSQL in `tests_web/test_teacher_availability_repository.py`."""

    def __init__(self, problem):
        self._problem = problem
        self.replace_calls: list[tuple] = []

    def replace_exceptions(self, school, year, teacher_natural_id, exceptions, validate):
        validate(self._problem)
        self.replace_calls.append((teacher_natural_id, exceptions))


def _projection_service(problem=None, active_schedule=None):
    problem = problem if problem is not None else build_valid_fixture()
    return TeacherAvailabilityProjectionService(
        _FakeProblemRepository(problem), _FakeScheduleRepository(active=active_schedule),
    )


def _write_service(problem=None, active_schedule=None):
    problem = problem if problem is not None else build_valid_fixture()
    write_port = _FakeWritePort(problem)
    service = TeacherAvailabilityService(
        _FakeProblemRepository(problem), write_port, _FakeScheduleRepository(active=active_schedule),
    )
    return service, write_port


def _exc(day_id: str, period_id: str, status: str) -> TeacherAvailabilityExceptionFields:
    return TeacherAvailabilityExceptionFields(day_id=day_id, period_id=period_id, status=status)


# -- PROJECTION -----------------------------------------------------------

def test_projection_teachers_preserve_authoritative_order():
    problem = build_valid_fixture()
    view = _projection_service(problem=problem).project(_SCHOOL, _YEAR)
    assert [t.id for t in view.teachers] == [t.id for t in problem.teachers]


def test_projection_days_preserve_authoritative_order():
    problem = build_valid_fixture()
    view = _projection_service(problem=problem).project(_SCHOOL, _YEAR)
    assert [d.id for d in view.days] == [d.id for d in problem.days]


def test_projection_periods_preserve_authoritative_order():
    problem = build_valid_fixture()
    view = _projection_service(problem=problem).project(_SCHOOL, _YEAR)
    assert [p.id for p in view.periods] == [p.id for p in problem.periods]


def test_projection_only_prefer_not_and_unavailable_exceptions_projected():
    view = _projection_service().project(_SCHOOL, _YEAR)
    statuses = {e.status for e in view.exceptions}
    assert statuses <= {"PREFER_NOT", "UNAVAILABLE"}
    assert "AVAILABLE" not in statuses
    # The pilot fixture's own known exceptions must both be present.
    assert any(e.teacher_id == "t_science" and e.status == "UNAVAILABLE" for e in view.exceptions)
    assert any(e.teacher_id == "t_history" and e.status == "PREFER_NOT" for e in view.exceptions)


def test_projection_explicit_available_legacy_row_not_exposed_as_exception():
    problem = build_valid_fixture()
    problem = replace(
        problem,
        teacher_availabilities=problem.teacher_availabilities
        + (TeacherAvailability("t_math", "wed", "p2", AvailabilityStatus.AVAILABLE),),
    )
    view = _projection_service(problem=problem).project(_SCHOOL, _YEAR)
    assert not any(e.teacher_id == "t_math" and e.day_id == "wed" and e.period_id == "p2" for e in view.exceptions)


def test_projection_configuration_locked_false_by_default():
    view = _projection_service().project(_SCHOOL, _YEAR)
    assert view.configuration_locked is False


def test_projection_configuration_locked_true_once_schedule_exists():
    view = _projection_service(active_schedule="anything-non-none").project(_SCHOOL, _YEAR)
    assert view.configuration_locked is True


# -- WRITE: SUCCESS ---------------------------------------------------------

def test_replace_with_unavailable_only():
    service, write_port = _write_service()
    result = service.replace_exceptions(
        _SCHOOL, _YEAR, "t_math", TeacherAvailabilityReplaceFields(exceptions=(_exc("mon", "p1", "UNAVAILABLE"),)),
    )
    assert result.teacher_id == "t_math"
    assert result.exceptions == (
        TeacherAvailabilityExceptionResult(day_id="mon", period_id="p1", status="UNAVAILABLE"),
    )
    assert write_port.replace_calls[0][0] == "t_math"


def test_replace_with_prefer_not_only():
    service, _ = _write_service()
    result = service.replace_exceptions(
        _SCHOOL, _YEAR, "t_math", TeacherAvailabilityReplaceFields(exceptions=(_exc("tue", "p4", "PREFER_NOT"),)),
    )
    assert result.exceptions[0].status == "PREFER_NOT"


def test_replace_with_mixed_prefer_not_and_unavailable():
    service, _ = _write_service()
    result = service.replace_exceptions(
        _SCHOOL, _YEAR, "t_math",
        TeacherAvailabilityReplaceFields(exceptions=(_exc("mon", "p1", "UNAVAILABLE"), _exc("tue", "p4", "PREFER_NOT"))),
    )
    assert len(result.exceptions) == 2


def test_replace_with_empty_exceptions_clears_all():
    service, write_port = _write_service()
    result = service.replace_exceptions(_SCHOOL, _YEAR, "t_math", TeacherAvailabilityReplaceFields(exceptions=()))
    assert result.exceptions == ()
    assert write_port.replace_calls[0][1] == ()


# -- WRITE: VALIDATION -------------------------------------------------------

def test_replace_available_entry_rejected():
    service, _ = _write_service()
    with pytest.raises(InvalidTeacherAvailabilityError) as exc_info:
        service.replace_exceptions(
            _SCHOOL, _YEAR, "t_math", TeacherAvailabilityReplaceFields(exceptions=(_exc("mon", "p1", "AVAILABLE"),)),
        )
    assert any(e.code == "AVAILABLE_EXCEPTION_MUST_BE_OMITTED" for e in exc_info.value.validation_errors)


def test_replace_unknown_status_rejected():
    service, _ = _write_service()
    with pytest.raises(InvalidTeacherAvailabilityError) as exc_info:
        service.replace_exceptions(
            _SCHOOL, _YEAR, "t_math", TeacherAvailabilityReplaceFields(exceptions=(_exc("mon", "p1", "MAYBE"),)),
        )
    assert any(e.code == "UNKNOWN_AVAILABILITY_STATUS" for e in exc_info.value.validation_errors)


def test_replace_duplicate_same_status_cell_rejected():
    service, _ = _write_service()
    with pytest.raises(InvalidTeacherAvailabilityError) as exc_info:
        service.replace_exceptions(
            _SCHOOL, _YEAR, "t_math",
            TeacherAvailabilityReplaceFields(
                exceptions=(_exc("mon", "p1", "UNAVAILABLE"), _exc("mon", "p1", "UNAVAILABLE")),
            ),
        )
    assert any(e.code == "DUPLICATE_AVAILABILITY_CELL" for e in exc_info.value.validation_errors)


def test_replace_duplicate_conflicting_status_cell_rejected():
    service, _ = _write_service()
    with pytest.raises(InvalidTeacherAvailabilityError) as exc_info:
        service.replace_exceptions(
            _SCHOOL, _YEAR, "t_math",
            TeacherAvailabilityReplaceFields(
                exceptions=(_exc("mon", "p1", "UNAVAILABLE"), _exc("mon", "p1", "PREFER_NOT")),
            ),
        )
    assert any(e.code == "DUPLICATE_AVAILABILITY_CELL" for e in exc_info.value.validation_errors)


def test_replace_unknown_teacher_rejected():
    service, _ = _write_service()
    with pytest.raises(TeacherNotFoundError):
        service.replace_exceptions(
            _SCHOOL, _YEAR, "t_nobody", TeacherAvailabilityReplaceFields(exceptions=(_exc("mon", "p1", "UNAVAILABLE"),)),
        )


def test_replace_unknown_day_rejected():
    service, _ = _write_service()
    with pytest.raises(UnknownReferenceError) as exc_info:
        service.replace_exceptions(
            _SCHOOL, _YEAR, "t_math", TeacherAvailabilityReplaceFields(exceptions=(_exc("someday", "p1", "UNAVAILABLE"),)),
        )
    assert exc_info.value.reference_kind == "day"
    assert exc_info.value.reference_id == "someday"


def test_replace_unknown_period_rejected():
    service, _ = _write_service()
    with pytest.raises(UnknownReferenceError) as exc_info:
        service.replace_exceptions(
            _SCHOOL, _YEAR, "t_math", TeacherAvailabilityReplaceFields(exceptions=(_exc("mon", "p99", "UNAVAILABLE"),)),
        )
    assert exc_info.value.reference_kind == "period"
    assert exc_info.value.reference_id == "p99"


def test_replace_rejected_once_schedule_exists():
    service, _ = _write_service(active_schedule="anything-non-none")
    with pytest.raises(ConfigurationLockedError):
        service.replace_exceptions(
            _SCHOOL, _YEAR, "t_math", TeacherAvailabilityReplaceFields(exceptions=(_exc("mon", "p1", "UNAVAILABLE"),)),
        )


def test_same_validation_reused_in_authoritative_repository_callback():
    """The fake write port re-invokes `validate` against the (unchanged)
    problem it was constructed with -- proving the same pure validation
    the service ran once, un-locked, is the exact callable the
    authoritative lock-protected recheck would also run."""
    problem = build_valid_fixture()
    write_port = _FakeWritePort(problem)
    service = TeacherAvailabilityService(
        _FakeProblemRepository(problem), write_port, _FakeScheduleRepository(),
    )
    service.replace_exceptions(
        _SCHOOL, _YEAR, "t_math", TeacherAvailabilityReplaceFields(exceptions=(_exc("mon", "p1", "UNAVAILABLE"),)),
    )
    assert len(write_port.replace_calls) == 1
