"""Pure orchestration/validation tests for Reserved Activities Slice
A1's `SpecialActivityService`. No database, no PostgreSQL -- all three
repository ports are small in-memory fakes; real-PostgreSQL
persistence/concurrency proof lives in
`tests_web/test_special_activity_repository.py`.

"Special Activity" is not a new domain entity -- it is the user-facing
name for `Activity(kind=CLUB)`.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from school_timetable.application.errors import (
    ConfigurationLockedError,
    DuplicateSpecialActivityError,
    InvalidSpecialActivityError,
    SpecialActivityInUseError,
    SpecialActivityNotFoundError,
)
from school_timetable.application.special_activity_models import SpecialActivityFields
from school_timetable.application.special_activity_service import SpecialActivityService
from school_timetable.domain.activities import Activity, ActivityKind
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
    genuine lock/reload-under-lock mechanics (and the derived-name
    `ReservedBlock` synchronization) are proven for real against
    PostgreSQL in `tests_web/test_special_activity_repository.py`;
    this fake exists only to exercise `SpecialActivityService`'s own
    orchestration and error propagation.

    `locked=True` simulates the real repository's own authoritative
    lock-rejection outcome -- raised BEFORE `validate` is ever invoked
    (Safe Configuration Changes, Slice B: `SpecialActivityService`
    itself no longer has any fast, un-locked precheck of its own)."""

    def __init__(self, problem, locked: bool = False):
        self._problem = problem
        self._locked = locked
        self.create_calls: list[tuple] = []
        self.update_calls: list[tuple] = []
        self.delete_calls: list[str] = []

    def create(self, school, year, special_activity_natural_id, name, validate):
        if self._locked:
            raise ConfigurationLockedError(school, year)
        validate(self._problem)
        self.create_calls.append((special_activity_natural_id, name))

    def update(self, school, year, special_activity_natural_id, name, validate):
        if self._locked:
            raise ConfigurationLockedError(school, year)
        validate(self._problem)
        self.update_calls.append((special_activity_natural_id, name))

    def delete(self, school, year, special_activity_natural_id, validate):
        if self._locked:
            raise ConfigurationLockedError(school, year)
        validate(self._problem)
        self.delete_calls.append(special_activity_natural_id)


def _service(problem=None, locked=False, activity_id_factory=None):
    problem = problem if problem is not None else build_valid_fixture()
    write_port = _FakeWritePort(problem, locked=locked)
    kwargs = {} if activity_id_factory is None else {"activity_id_factory": activity_id_factory}
    service = SpecialActivityService(_FakeProblemRepository(problem), write_port, **kwargs)
    return service, write_port


# -- CREATE -----------------------------------------------------------------

def test_create_normal_special_activity_succeeds():
    service, write_port = _service()
    result = service.create(_SCHOOL, _YEAR, SpecialActivityFields(name="Debate Club"))
    assert result.name == "Debate Club"
    assert write_port.create_calls[0] == (result.id, "Debate Club")


def test_create_trims_outer_whitespace():
    service, write_port = _service()
    result = service.create(_SCHOOL, _YEAR, SpecialActivityFields(name="  Debate Club  "))
    assert result.name == "Debate Club"
    assert write_port.create_calls[0][1] == "Debate Club"


def test_create_blank_name_rejected():
    service, _ = _service()
    with pytest.raises(InvalidSpecialActivityError) as exc_info:
        service.create(_SCHOOL, _YEAR, SpecialActivityFields(name="   "))
    assert any(e.code == "BLANK_SPECIAL_ACTIVITY_NAME" for e in exc_info.value.validation_errors)


def test_create_accepts_georgian_unicode_name():
    service, _ = _service()
    result = service.create(_SCHOOL, _YEAR, SpecialActivityFields(name="ჭადრაკის კლუბი"))
    assert result.name == "ჭადრაკის კლუბი"


def test_create_accepts_punctuation_name():
    service, _ = _service()
    result = service.create(_SCHOOL, _YEAR, SpecialActivityFields(name="Art & Design Club"))
    assert result.name == "Art & Design Club"


def test_create_generates_activity_prefixed_id():
    service, write_port = _service()
    result = service.create(_SCHOOL, _YEAR, SpecialActivityFields(name="Debate Club"))
    assert result.id.startswith("activity_")
    assert len(result.id) == len("activity_") + 32
    assert write_port.create_calls[0][0] == result.id


def test_create_duplicate_other_club_rejected():
    # "Chess Club" already exists in the fixture (kind=CLUB).
    service, _ = _service()
    with pytest.raises(DuplicateSpecialActivityError):
        service.create(_SCHOOL, _YEAR, SpecialActivityFields(name="Chess Club"))
    with pytest.raises(DuplicateSpecialActivityError):
        service.create(_SCHOOL, _YEAR, SpecialActivityFields(name="  Chess Club  "))


def test_create_case_variant_allowed():
    service, _ = _service()
    result = service.create(_SCHOOL, _YEAR, SpecialActivityFields(name="chess club"))
    assert result.name == "chess club"


def test_create_same_name_as_ordinary_subject_allowed():
    # "Mathematics" is Activity(kind=ORDINARY) in the fixture -- an
    # identically-named CLUB special activity must still be creatable.
    service, _ = _service()
    result = service.create(_SCHOOL, _YEAR, SpecialActivityFields(name="Mathematics"))
    assert result.name == "Mathematics"


def test_create_uses_injected_deterministic_id_factory():
    service, write_port = _service(activity_id_factory=lambda: "activity_deterministic_test_id")
    result = service.create(_SCHOOL, _YEAR, SpecialActivityFields(name="Debate Club"))
    assert result.id == "activity_deterministic_test_id"
    assert write_port.create_calls[0] == ("activity_deterministic_test_id", "Debate Club")


def test_create_rejected_when_configuration_locked():
    service, _ = _service(locked=True)
    with pytest.raises(ConfigurationLockedError):
        service.create(_SCHOOL, _YEAR, SpecialActivityFields(name="Debate Club"))


# -- UPDATE -------------------------------------------------------------

def test_update_renames_special_activity():
    service, write_port = _service()
    result = service.update(_SCHOOL, _YEAR, "club_chess", SpecialActivityFields(name="Strategy Games Club"))
    assert result.id == "club_chess"
    assert result.name == "Strategy Games Club"
    assert write_port.update_calls[0] == ("club_chess", "Strategy Games Club")


def test_update_preserves_natural_id():
    service, _ = _service()
    result = service.update(_SCHOOL, _YEAR, "club_chess", SpecialActivityFields(name="Renamed"))
    assert result.id == "club_chess"


def test_update_duplicate_other_club_rejected():
    service, _ = _service()
    with pytest.raises(DuplicateSpecialActivityError):
        service.update(_SCHOOL, _YEAR, "club_chess", SpecialActivityFields(name="Robotics Club"))


def test_update_same_name_as_ordinary_subject_allowed():
    service, _ = _service()
    result = service.update(_SCHOOL, _YEAR, "club_chess", SpecialActivityFields(name="Mathematics"))
    assert result.name == "Mathematics"


def test_update_same_special_activity_retaining_own_name_allowed():
    service, write_port = _service()
    result = service.update(_SCHOOL, _YEAR, "club_chess", SpecialActivityFields(name="Chess Club"))
    assert result.name == "Chess Club"
    assert write_port.update_calls[0] == ("club_chess", "Chess Club")


def test_update_trims_value():
    service, write_port = _service()
    service.update(_SCHOOL, _YEAR, "club_chess", SpecialActivityFields(name="  Renamed  "))
    assert write_port.update_calls[0] == ("club_chess", "Renamed")


def test_update_missing_id_rejected():
    service, _ = _service()
    with pytest.raises(SpecialActivityNotFoundError):
        service.update(_SCHOOL, _YEAR, "no-such-activity", SpecialActivityFields(name="X"))


def test_update_ordinary_id_rejected_as_not_found():
    service, _ = _service()
    with pytest.raises(SpecialActivityNotFoundError):
        service.update(_SCHOOL, _YEAR, "math", SpecialActivityFields(name="Renamed"))


def test_update_rejected_when_configuration_locked():
    service, _ = _service(locked=True)
    with pytest.raises(ConfigurationLockedError):
        service.update(_SCHOOL, _YEAR, "club_chess", SpecialActivityFields(name="Renamed"))


# -- DELETE -----------------------------------------------------------------

def _problem_with_unused_special_activity(problem, special_activity_id="unused_special_activity"):
    return replace(
        problem,
        activities=problem.activities + (Activity(id=special_activity_id, name="Unused", kind=ActivityKind.CLUB),),
    )


def test_delete_unused_club_succeeds():
    problem = _problem_with_unused_special_activity(build_valid_fixture())
    service, write_port = _service(problem=problem)
    service.delete(_SCHOOL, _YEAR, "unused_special_activity")
    assert write_port.delete_calls == ["unused_special_activity"]


def test_delete_missing_rejected():
    service, _ = _service()
    with pytest.raises(SpecialActivityNotFoundError):
        service.delete(_SCHOOL, _YEAR, "no-such-activity")


def test_delete_ordinary_rejected_as_not_found():
    service, _ = _service()
    with pytest.raises(SpecialActivityNotFoundError):
        service.delete(_SCHOOL, _YEAR, "math")


def test_delete_reserved_block_blocker():
    # "club_chess" is targeted by a ReservedBlock in the fixture.
    service, _ = _service()
    with pytest.raises(SpecialActivityInUseError) as exc_info:
        service.delete(_SCHOOL, _YEAR, "club_chess")
    assert exc_info.value.referenced_by == ("RESERVED_BLOCK",)


def test_delete_unreferenced_club_with_no_reserved_block_succeeds():
    problem = _problem_with_unused_special_activity(build_valid_fixture())
    problem = replace(
        problem,
        reserved_blocks=problem.reserved_blocks + (
            ReservedBlock(
                id="club_extra", name="Extra Club", activity_id="club_chess",
                class_sections=("8a",), slots=(TimeSlot("mon", "p1"),),
            ),
        ),
    )
    service, write_port = _service(problem=problem)
    service.delete(_SCHOOL, _YEAR, "unused_special_activity")
    assert write_port.delete_calls == ["unused_special_activity"]


def test_delete_rejected_when_configuration_locked():
    # An unreferenced club, so the service's OWN validate-precheck
    # (which runs before the write port is ever reached) passes --
    # otherwise SpecialActivityInUseError would fire first.
    problem = _problem_with_unused_special_activity(build_valid_fixture())
    service, _ = _service(problem=problem, locked=True)
    with pytest.raises(ConfigurationLockedError):
        service.delete(_SCHOOL, _YEAR, "unused_special_activity")
