"""Pure orchestration/validation tests for Real-School Setup MVP Slice
D's `SubjectService`. No database, no PostgreSQL -- all three
repository ports are small in-memory fakes; real-PostgreSQL
persistence/concurrency proof lives in
`tests_web/test_subject_repository.py`.

"Subject" is not a new domain entity -- it is the user-facing name for
`Activity(kind=ORDINARY)`.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from school_timetable.application.errors import (
    ConfigurationLockedError,
    DuplicateSubjectError,
    InvalidSubjectError,
    SubjectInUseError,
    SubjectNotFoundError,
)
from school_timetable.application.subject_models import SubjectFields
from school_timetable.application.subject_service import SubjectService
from school_timetable.domain.activities import Activity
from school_timetable.domain.blocks import ReservedBlock
from school_timetable.domain.calendar import TimeSlot
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
    PostgreSQL in `tests_web/test_subject_repository.py`; this fake
    exists only to exercise `SubjectService`'s own orchestration and
    error propagation.

    `locked=True` simulates the real repository's own authoritative
    lock-rejection outcome -- raised BEFORE `validate` is ever invoked
    (Safe Configuration Changes, Slice B: `SubjectService` itself no
    longer has any fast, un-locked precheck of its own)."""

    def __init__(self, problem, locked: bool = False):
        self._problem = problem
        self._locked = locked
        self.create_calls: list[tuple] = []
        self.update_calls: list[tuple] = []
        self.delete_calls: list[str] = []

    def create(self, school, year, subject_natural_id, name, validate):
        if self._locked:
            raise ConfigurationLockedError(school, year)
        validate(self._problem)
        self.create_calls.append((subject_natural_id, name))

    def update(self, school, year, subject_natural_id, name, validate):
        if self._locked:
            raise ConfigurationLockedError(school, year)
        validate(self._problem)
        self.update_calls.append((subject_natural_id, name))

    def delete(self, school, year, subject_natural_id, validate):
        if self._locked:
            raise ConfigurationLockedError(school, year)
        validate(self._problem)
        self.delete_calls.append(subject_natural_id)


def _service(problem=None, locked=False, activity_id_factory=None):
    problem = problem if problem is not None else build_valid_fixture()
    write_port = _FakeWritePort(problem, locked=locked)
    kwargs = {} if activity_id_factory is None else {"activity_id_factory": activity_id_factory}
    service = SubjectService(_FakeProblemRepository(problem), write_port, **kwargs)
    return service, write_port


# -- CREATE -----------------------------------------------------------------

def test_create_normal_subject_succeeds():
    service, write_port = _service()
    result = service.create(_SCHOOL, _YEAR, SubjectFields(name="Physics"))
    assert result.name == "Physics"
    assert write_port.create_calls[0] == (result.id, "Physics")


def test_create_trims_outer_whitespace():
    service, write_port = _service()
    result = service.create(_SCHOOL, _YEAR, SubjectFields(name="  Physics  "))
    assert result.name == "Physics"
    assert write_port.create_calls[0][1] == "Physics"


def test_create_blank_name_rejected():
    service, _ = _service()
    with pytest.raises(InvalidSubjectError) as exc_info:
        service.create(_SCHOOL, _YEAR, SubjectFields(name="   "))
    assert any(e.code == "BLANK_SUBJECT_NAME" for e in exc_info.value.validation_errors)


def test_create_accepts_georgian_unicode_name():
    service, _ = _service()
    result = service.create(_SCHOOL, _YEAR, SubjectFields(name="ქართული ენა"))
    assert result.name == "ქართული ენა"


def test_create_accepts_punctuation_name():
    service, _ = _service()
    result = service.create(_SCHOOL, _YEAR, SubjectFields(name="Art & Design"))
    assert result.name == "Art & Design"


def test_create_generates_activity_prefixed_id():
    service, write_port = _service()
    result = service.create(_SCHOOL, _YEAR, SubjectFields(name="Physics"))
    assert result.id.startswith("activity_")
    assert len(result.id) == len("activity_") + 32
    assert write_port.create_calls[0][0] == result.id


def test_create_duplicate_other_ordinary_rejected():
    # "Mathematics" already exists in the fixture (kind=ORDINARY).
    service, _ = _service()
    with pytest.raises(DuplicateSubjectError):
        service.create(_SCHOOL, _YEAR, SubjectFields(name="Mathematics"))
    with pytest.raises(DuplicateSubjectError):
        service.create(_SCHOOL, _YEAR, SubjectFields(name="  Mathematics  "))


def test_create_case_variant_allowed():
    service, _ = _service()
    result = service.create(_SCHOOL, _YEAR, SubjectFields(name="mathematics"))
    assert result.name == "mathematics"


def test_create_same_name_as_club_allowed():
    # "Chess Club" is Activity(kind=CLUB) in the fixture -- an
    # identically-named ORDINARY subject must still be creatable.
    service, _ = _service()
    result = service.create(_SCHOOL, _YEAR, SubjectFields(name="Chess Club"))
    assert result.name == "Chess Club"


def test_create_uses_injected_deterministic_id_factory():
    service, write_port = _service(activity_id_factory=lambda: "activity_deterministic_test_id")
    result = service.create(_SCHOOL, _YEAR, SubjectFields(name="Physics"))
    assert result.id == "activity_deterministic_test_id"
    assert write_port.create_calls[0] == ("activity_deterministic_test_id", "Physics")


def test_create_rejected_when_configuration_locked():
    service, _ = _service(locked=True)
    with pytest.raises(ConfigurationLockedError):
        service.create(_SCHOOL, _YEAR, SubjectFields(name="Physics"))


# -- UPDATE -------------------------------------------------------------

def test_update_renames_subject():
    service, write_port = _service()
    result = service.update(_SCHOOL, _YEAR, "math", SubjectFields(name="Advanced Mathematics"))
    assert result.id == "math"
    assert result.name == "Advanced Mathematics"
    assert write_port.update_calls[0] == ("math", "Advanced Mathematics")


def test_update_preserves_natural_id():
    service, _ = _service()
    result = service.update(_SCHOOL, _YEAR, "math", SubjectFields(name="Renamed"))
    assert result.id == "math"


def test_update_duplicate_other_ordinary_rejected():
    service, _ = _service()
    with pytest.raises(DuplicateSubjectError):
        service.update(_SCHOOL, _YEAR, "math", SubjectFields(name="Science"))


def test_update_same_name_as_club_allowed():
    service, _ = _service()
    result = service.update(_SCHOOL, _YEAR, "math", SubjectFields(name="Chess Club"))
    assert result.name == "Chess Club"


def test_update_same_subject_retaining_own_name_allowed():
    service, write_port = _service()
    result = service.update(_SCHOOL, _YEAR, "math", SubjectFields(name="Mathematics"))
    assert result.name == "Mathematics"
    assert write_port.update_calls[0] == ("math", "Mathematics")


def test_update_trims_value():
    service, write_port = _service()
    service.update(_SCHOOL, _YEAR, "math", SubjectFields(name="  Renamed  "))
    assert write_port.update_calls[0] == ("math", "Renamed")


def test_update_missing_id_rejected():
    service, _ = _service()
    with pytest.raises(SubjectNotFoundError):
        service.update(_SCHOOL, _YEAR, "no-such-activity", SubjectFields(name="X"))


def test_update_club_id_rejected_as_not_found():
    service, _ = _service()
    with pytest.raises(SubjectNotFoundError):
        service.update(_SCHOOL, _YEAR, "club_chess", SubjectFields(name="Renamed"))


def test_update_rejected_when_configuration_locked():
    service, _ = _service(locked=True)
    with pytest.raises(ConfigurationLockedError):
        service.update(_SCHOOL, _YEAR, "math", SubjectFields(name="Renamed"))


# -- DELETE -----------------------------------------------------------------

def _problem_with_unused_subject(problem, subject_id="unused_subject"):
    return replace(
        problem, activities=problem.activities + (Activity(id=subject_id, name="Unused"),),
    )


def test_delete_unused_ordinary_succeeds():
    problem = _problem_with_unused_subject(build_valid_fixture())
    service, write_port = _service(problem=problem)
    service.delete(_SCHOOL, _YEAR, "unused_subject")
    assert write_port.delete_calls == ["unused_subject"]


def test_delete_missing_rejected():
    service, _ = _service()
    with pytest.raises(SubjectNotFoundError):
        service.delete(_SCHOOL, _YEAR, "no-such-activity")


def test_delete_club_rejected_as_not_found():
    service, _ = _service()
    with pytest.raises(SubjectNotFoundError):
        service.delete(_SCHOOL, _YEAR, "club_chess")


def test_delete_teaching_requirement_blocker():
    # "math" is targeted by several TeachingRequirements in the fixture.
    service, _ = _service()
    with pytest.raises(SubjectInUseError) as exc_info:
        service.delete(_SCHOOL, _YEAR, "math")
    assert exc_info.value.referenced_by == ("TEACHING_REQUIREMENT",)


def test_delete_reserved_block_blocker():
    problem = _problem_with_unused_subject(build_valid_fixture())
    problem = replace(
        problem,
        reserved_blocks=problem.reserved_blocks + (
            ReservedBlock(
                id="club_extra", name="Extra Club", activity_id="unused_subject",
                class_sections=("8a",), slots=(TimeSlot("mon", "p1"),),
            ),
        ),
    )
    service, _ = _service(problem=problem)
    with pytest.raises(SubjectInUseError) as exc_info:
        service.delete(_SCHOOL, _YEAR, "unused_subject")
    assert exc_info.value.referenced_by == ("RESERVED_BLOCK",)


def test_delete_combined_blockers_deterministic_order():
    problem = _problem_with_unused_subject(build_valid_fixture())
    problem = replace(
        problem,
        teaching_requirements=problem.teaching_requirements + (
            replace(problem.teaching_requirements[0], id="__extra__", activity_id="unused_subject"),
        ),
        reserved_blocks=problem.reserved_blocks + (
            ReservedBlock(
                id="club_extra", name="Extra Club", activity_id="unused_subject",
                class_sections=("8a",), slots=(TimeSlot("mon", "p1"),),
            ),
        ),
    )
    service, _ = _service(problem=problem)
    with pytest.raises(SubjectInUseError) as exc_info:
        service.delete(_SCHOOL, _YEAR, "unused_subject")
    assert exc_info.value.referenced_by == ("TEACHING_REQUIREMENT", "RESERVED_BLOCK")


def test_delete_rejected_when_configuration_locked():
    # An unreferenced subject, so the service's OWN validate-precheck
    # (which runs before the write port is ever reached) passes --
    # otherwise SubjectInUseError would fire first.
    problem = _problem_with_unused_subject(build_valid_fixture())
    service, _ = _service(problem=problem, locked=True)
    with pytest.raises(ConfigurationLockedError):
        service.delete(_SCHOOL, _YEAR, "unused_subject")
