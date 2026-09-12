"""Pure orchestration/validation tests for Real-School Setup MVP Slice
B's `TeacherService`. No database, no PostgreSQL -- all three
repository ports are small in-memory fakes; real-PostgreSQL
persistence/concurrency proof lives in
`tests_web/test_teacher_repository.py`.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from school_timetable.application.errors import (
    ConfigurationLockedError,
    InvalidTeacherError,
    TeacherInUseError,
    TeacherNotFoundError,
)
from school_timetable.application.teacher_models import TeacherFields
from school_timetable.application.teacher_service import TeacherService
from school_timetable.domain.blocks import ReservedBlock
from school_timetable.domain.calendar import TimeSlot
from school_timetable.domain.people import AvailabilityStatus, Teacher, TeacherAvailability
from school_timetable.fixtures.valid_fixture import build_valid_fixture

_SCHOOL = "synthetic-school"
_YEAR = "ay-2026"


class _FakeProblemRepository:
    def __init__(self, problem):
        self._problem = problem

    def load_by_school_and_year(self, school_natural_id, academic_year_natural_id):
        return self._problem


class _FakeWritePort:
    """Simulates the authoritative, lock-protected recheck by simply
    re-invoking `validate` against the same (unchanged) problem -- the
    genuine lock/reload-under-lock mechanics are proven for real against
    PostgreSQL in `tests_web/test_teacher_repository.py`; this fake
    exists only to exercise `TeacherService`'s own orchestration and
    error propagation.

    `locked=True` simulates the real repository's own authoritative
    `configuration_write_lock.reject_if_configuration_locked` outcome --
    raised BEFORE `validate` is ever invoked, exactly like the real
    write path (Safe Configuration Changes, Slice B: `TeacherService`
    itself no longer has any fast, un-locked precheck of its own; the
    repository is the sole source of truth for whether configuration is
    currently writable)."""

    def __init__(self, problem, locked: bool = False):
        self._problem = problem
        self._locked = locked
        self.create_calls: list[tuple] = []
        self.update_calls: list[tuple] = []
        self.delete_calls: list[str] = []

    def create(self, school, year, natural_id, first_name, last_name, validate):
        if self._locked:
            raise ConfigurationLockedError(school, year)
        validate(self._problem)
        self.create_calls.append((natural_id, first_name, last_name))

    def update(self, school, year, natural_id, first_name, last_name, validate):
        if self._locked:
            raise ConfigurationLockedError(school, year)
        validate(self._problem)
        self.update_calls.append((natural_id, first_name, last_name))

    def delete(self, school, year, natural_id, validate):
        if self._locked:
            raise ConfigurationLockedError(school, year)
        validate(self._problem)
        self.delete_calls.append(natural_id)


def _service(problem=None, locked=False, id_factory=None):
    problem = problem if problem is not None else build_valid_fixture()
    write_port = _FakeWritePort(problem, locked=locked)
    kwargs = {} if id_factory is None else {"id_factory": id_factory}
    service = TeacherService(_FakeProblemRepository(problem), write_port, **kwargs)
    return service, write_port


# -- CREATE -------------------------------------------------------------

def test_create_normal_teacher_succeeds():
    service, write_port = _service()
    result = service.create(_SCHOOL, _YEAR, TeacherFields(first_name="Nino", last_name="Beridze"))
    assert result.first_name == "Nino"
    assert result.last_name == "Beridze"
    assert result.name == "Nino Beridze"
    assert write_port.create_calls[0][0] == result.id


def test_create_trims_outer_whitespace():
    service, write_port = _service()
    result = service.create(_SCHOOL, _YEAR, TeacherFields(first_name="  Nino  ", last_name="  Beridze  "))
    assert result.first_name == "Nino"
    assert result.last_name == "Beridze"
    assert write_port.create_calls[0][1:] == ("Nino", "Beridze")


def test_create_accepts_georgian_unicode_names():
    service, _ = _service()
    result = service.create(_SCHOOL, _YEAR, TeacherFields(first_name="ნინო", last_name="ბერიძე"))
    assert result.first_name == "ნინო"
    assert result.last_name == "ბერიძე"
    assert result.name == "ნინო ბერიძე"


def test_create_accepts_apostrophe_hyphen_internal_spaces():
    service, _ = _service()
    result = service.create(
        _SCHOOL, _YEAR, TeacherFields(first_name="Mary-Jane", last_name="O'Brien Smith"),
    )
    assert result.first_name == "Mary-Jane"
    assert result.last_name == "O'Brien Smith"


def test_create_blank_first_name_rejected():
    service, _ = _service()
    with pytest.raises(InvalidTeacherError) as exc_info:
        service.create(_SCHOOL, _YEAR, TeacherFields(first_name="   ", last_name="Beridze"))
    assert any(e.code == "BLANK_FIRST_NAME" for e in exc_info.value.validation_errors)


def test_create_blank_last_name_rejected():
    service, _ = _service()
    with pytest.raises(InvalidTeacherError) as exc_info:
        service.create(_SCHOOL, _YEAR, TeacherFields(first_name="Nino", last_name="   "))
    assert any(e.code == "BLANK_LAST_NAME" for e in exc_info.value.validation_errors)


def test_create_same_name_teachers_allowed():
    service, write_port = _service()
    first = service.create(_SCHOOL, _YEAR, TeacherFields(first_name="Nino", last_name="Beridze"))
    second = service.create(_SCHOOL, _YEAR, TeacherFields(first_name="Nino", last_name="Beridze"))
    assert first.id != second.id
    assert len(write_port.create_calls) == 2


def test_create_generates_an_opaque_backend_natural_id():
    service, _ = _service()
    result = service.create(_SCHOOL, _YEAR, TeacherFields(first_name="George", last_name="Kakochashvili"))
    assert result.id.startswith("teacher_")
    assert len(result.id) == len("teacher_") + 32  # full uuid4 hex, never truncated


def test_create_uses_an_injected_id_factory_deterministically():
    service, _ = _service(id_factory=lambda: "teacher_deterministic_test_id")
    result = service.create(_SCHOOL, _YEAR, TeacherFields(first_name="George", last_name="Kakochashvili"))
    assert result.id == "teacher_deterministic_test_id"


# -- UPDATE ---------------------------------------------------------------

def test_update_changes_first_and_last_name():
    service, write_port = _service()
    result = service.update(_SCHOOL, _YEAR, "t_math", TeacherFields(first_name="New", last_name="Name"))
    assert result.id == "t_math"
    assert result.first_name == "New"
    assert result.last_name == "Name"
    assert write_port.update_calls[0] == ("t_math", "New", "Name")


def test_update_preserves_natural_id():
    service, _ = _service()
    result = service.update(_SCHOOL, _YEAR, "t_science", TeacherFields(first_name="A", last_name="B"))
    assert result.id == "t_science"


def test_update_trims_values():
    service, write_port = _service()
    service.update(_SCHOOL, _YEAR, "t_math", TeacherFields(first_name="  New  ", last_name="  Name  "))
    assert write_port.update_calls[0] == ("t_math", "New", "Name")


def test_update_invalid_rejected():
    service, _ = _service()
    with pytest.raises(InvalidTeacherError):
        service.update(_SCHOOL, _YEAR, "t_math", TeacherFields(first_name="", last_name="Name"))


def test_update_missing_teacher_rejected():
    service, _ = _service()
    with pytest.raises(TeacherNotFoundError):
        service.update(_SCHOOL, _YEAR, "t_nobody", TeacherFields(first_name="A", last_name="B"))


# -- DELETE -----------------------------------------------------------------

def _problem_with_unused_teacher(problem, teacher_id="t_unused"):
    return replace(problem, teachers=problem.teachers + (Teacher(id=teacher_id, first_name="Unused", last_name=""),))


def test_delete_unused_teacher_succeeds():
    problem = _problem_with_unused_teacher(build_valid_fixture())
    service, write_port = _service(problem=problem)
    service.delete(_SCHOOL, _YEAR, "t_unused")
    assert write_port.delete_calls == ["t_unused"]


def test_delete_missing_teacher_rejected():
    service, _ = _service()
    with pytest.raises(TeacherNotFoundError):
        service.delete(_SCHOOL, _YEAR, "t_nobody")


def test_delete_teaching_requirement_reference_rejected():
    # t_math is referenced by several TeachingRequirements in the fixture.
    service, _ = _service()
    with pytest.raises(TeacherInUseError) as exc_info:
        service.delete(_SCHOOL, _YEAR, "t_math")
    assert exc_info.value.referenced_by == ("TEACHING_REQUIREMENT",)


def test_delete_teacher_availability_reference_rejected():
    problem = build_valid_fixture()
    problem = _problem_with_unused_teacher(problem)
    problem = replace(
        problem,
        teacher_availabilities=problem.teacher_availabilities
        + (TeacherAvailability("t_unused", "mon", "p1", AvailabilityStatus.UNAVAILABLE),),
    )
    service, _ = _service(problem=problem)
    with pytest.raises(TeacherInUseError) as exc_info:
        service.delete(_SCHOOL, _YEAR, "t_unused")
    assert exc_info.value.referenced_by == ("TEACHER_AVAILABILITY",)


def test_delete_reserved_block_reference_rejected():
    problem = build_valid_fixture()
    problem = _problem_with_unused_teacher(problem)
    problem = replace(
        problem,
        reserved_blocks=problem.reserved_blocks
        + (ReservedBlock(
            id="club_extra", name="Extra Club", activity_id="club_chess",
            class_sections=("8a",), slots=(TimeSlot("mon", "p1"),), teacher_id="t_unused",
        ),),
    )
    service, _ = _service(problem=problem)
    with pytest.raises(TeacherInUseError) as exc_info:
        service.delete(_SCHOOL, _YEAR, "t_unused")
    assert exc_info.value.referenced_by == ("RESERVED_BLOCK",)


def test_delete_multiple_reference_kinds_deterministic_order():
    problem = build_valid_fixture()
    problem = _problem_with_unused_teacher(problem)
    problem = replace(
        problem,
        teaching_requirements=problem.teaching_requirements + (
            replace(problem.teaching_requirements[0], id="__extra__", teacher_id="t_unused"),
        ),
        teacher_availabilities=problem.teacher_availabilities
        + (TeacherAvailability("t_unused", "mon", "p1", AvailabilityStatus.UNAVAILABLE),),
        reserved_blocks=problem.reserved_blocks
        + (ReservedBlock(
            id="club_extra", name="Extra Club", activity_id="club_chess",
            class_sections=("8a",), slots=(TimeSlot("mon", "p1"),), teacher_id="t_unused",
        ),),
    )
    service, _ = _service(problem=problem)
    with pytest.raises(TeacherInUseError) as exc_info:
        service.delete(_SCHOOL, _YEAR, "t_unused")
    assert exc_info.value.referenced_by == ("TEACHING_REQUIREMENT", "TEACHER_AVAILABILITY", "RESERVED_BLOCK")


# -- LOCK -----------------------------------------------------------------
# Safe Configuration Changes, Slice B: `TeacherService` itself no longer
# decides whether configuration is locked (it has no `ScheduleVersionRepository`
# dependency at all any more) -- that authority moved entirely to the
# repository (`configuration_write_lock.reject_if_configuration_locked`,
# proven for real in `tests_web/test_teacher_repository.py`). These tests
# now prove only that the SERVICE correctly propagates a
# `ConfigurationLockedError` the write port raises, never swallowing or
# reinterpreting it.

def test_create_rejected_when_configuration_locked():
    service, _ = _service(locked=True)
    with pytest.raises(ConfigurationLockedError):
        service.create(_SCHOOL, _YEAR, TeacherFields(first_name="Nino", last_name="Beridze"))


def test_update_rejected_when_configuration_locked():
    service, _ = _service(locked=True)
    with pytest.raises(ConfigurationLockedError):
        service.update(_SCHOOL, _YEAR, "t_math", TeacherFields(first_name="A", last_name="B"))


def test_delete_rejected_when_configuration_locked():
    # An unreferenced teacher, so the service's OWN validate-precheck
    # (which runs before the write port is ever reached) passes --
    # otherwise TeacherInUseError would fire first, never exercising
    # the write port's lock rejection at all.
    problem = _problem_with_unused_teacher(build_valid_fixture())
    service, _ = _service(problem=problem, locked=True)
    with pytest.raises(ConfigurationLockedError):
        service.delete(_SCHOOL, _YEAR, "t_unused")
