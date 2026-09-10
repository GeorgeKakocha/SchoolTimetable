"""Pure orchestration/validation tests for Resources Slice A's
`ResourceService`. No database, no PostgreSQL -- all three repository
ports are small in-memory fakes; real-PostgreSQL persistence/
concurrency proof lives in `tests_web/test_resource_repository.py`.

`Resource` is the existing `domain.resources.Resource(id, name,
capacity=1)` -- not a new/redesigned entity; this slice only adds the
missing catalog write use case over it.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from school_timetable.application.errors import (
    ConfigurationLockedError,
    DuplicateResourceError,
    InvalidResourceError,
    ResourceInUseError,
    ResourceNotFoundError,
)
from school_timetable.application.resource_models import ResourceFields
from school_timetable.application.resource_service import ResourceService
from school_timetable.domain.resources import Resource
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
    genuine lock/reload-under-lock mechanics are proven for real
    against PostgreSQL in `tests_web/test_resource_repository.py`;
    this fake exists only to exercise `ResourceService`'s own
    orchestration and error propagation."""

    def __init__(self, problem):
        self._problem = problem
        self.create_calls: list[tuple] = []
        self.update_calls: list[tuple] = []
        self.delete_calls: list[str] = []

    def create(self, school, year, resource_natural_id, name, capacity, validate):
        validate(self._problem)
        self.create_calls.append((resource_natural_id, name, capacity))

    def update(self, school, year, resource_natural_id, name, capacity, validate):
        validate(self._problem)
        self.update_calls.append((resource_natural_id, name, capacity))

    def delete(self, school, year, resource_natural_id, validate):
        validate(self._problem)
        self.delete_calls.append(resource_natural_id)


def _service(problem=None, active_schedule=None, resource_id_factory=None):
    problem = problem if problem is not None else build_valid_fixture()
    write_port = _FakeWritePort(problem)
    kwargs = {} if resource_id_factory is None else {"resource_id_factory": resource_id_factory}
    service = ResourceService(
        _FakeProblemRepository(problem), write_port, _FakeScheduleRepository(active=active_schedule), **kwargs,
    )
    return service, write_port


def _problem_with_unused_resource(problem, resource_id="unused_resource"):
    return replace(problem, resources=problem.resources + (Resource(id=resource_id, name="Spare Room", capacity=1),))


# -- CREATE -------------------------------------------------------------

def test_create_normal_resource_succeeds():
    service, write_port = _service()
    result = service.create(_SCHOOL, _YEAR, ResourceFields(name="Music Room", capacity=1))
    assert result.name == "Music Room"
    assert result.capacity == 1
    assert write_port.create_calls[0] == (result.id, "Music Room", 1)


def test_create_trims_outer_whitespace():
    service, write_port = _service()
    result = service.create(_SCHOOL, _YEAR, ResourceFields(name="  Music Room  ", capacity=1))
    assert result.name == "Music Room"
    assert write_port.create_calls[0][1] == "Music Room"


def test_create_blank_name_rejected():
    service, _ = _service()
    with pytest.raises(InvalidResourceError) as exc_info:
        service.create(_SCHOOL, _YEAR, ResourceFields(name="   ", capacity=1))
    assert any(e.code == "BLANK_RESOURCE_NAME" for e in exc_info.value.validation_errors)


def test_create_capacity_zero_rejected():
    service, _ = _service()
    with pytest.raises(InvalidResourceError) as exc_info:
        service.create(_SCHOOL, _YEAR, ResourceFields(name="Music Room", capacity=0))
    assert any(e.code == "INVALID_RESOURCE_CAPACITY" for e in exc_info.value.validation_errors)


def test_create_capacity_negative_rejected():
    service, _ = _service()
    with pytest.raises(InvalidResourceError) as exc_info:
        service.create(_SCHOOL, _YEAR, ResourceFields(name="Music Room", capacity=-1))
    assert any(e.code == "INVALID_RESOURCE_CAPACITY" for e in exc_info.value.validation_errors)


def test_create_capacity_greater_than_one_accepted():
    service, write_port = _service()
    result = service.create(_SCHOOL, _YEAR, ResourceFields(name="Laptop Carts", capacity=3))
    assert result.capacity == 3
    assert write_port.create_calls[0][2] == 3


def test_create_accepts_georgian_unicode_name():
    service, _ = _service()
    result = service.create(_SCHOOL, _YEAR, ResourceFields(name="სპორტული დარბაზი", capacity=1))
    assert result.name == "სპორტული დარბაზი"


def test_create_accepts_punctuation_name():
    service, _ = _service()
    result = service.create(_SCHOOL, _YEAR, ResourceFields(name="Lab & Workshop", capacity=1))
    assert result.name == "Lab & Workshop"


def test_create_generates_resource_prefixed_id():
    service, write_port = _service()
    result = service.create(_SCHOOL, _YEAR, ResourceFields(name="Music Room", capacity=1))
    assert result.id.startswith("resource_")
    assert len(result.id) == len("resource_") + 32
    assert write_port.create_calls[0][0] == result.id


def test_create_duplicate_other_resource_rejected():
    # "Indoor Gym" already exists in the fixture.
    service, _ = _service()
    with pytest.raises(DuplicateResourceError):
        service.create(_SCHOOL, _YEAR, ResourceFields(name="Indoor Gym", capacity=1))
    with pytest.raises(DuplicateResourceError):
        service.create(_SCHOOL, _YEAR, ResourceFields(name="  Indoor Gym  ", capacity=1))


def test_create_case_variant_allowed():
    service, _ = _service()
    result = service.create(_SCHOOL, _YEAR, ResourceFields(name="indoor gym", capacity=1))
    assert result.name == "indoor gym"


def test_create_same_name_as_subject_allowed():
    # "Mathematics" is an ORDINARY Subject in the fixture -- an
    # identically-named Resource must still be creatable, since
    # Resource is its own separate catalog.
    service, _ = _service()
    result = service.create(_SCHOOL, _YEAR, ResourceFields(name="Mathematics", capacity=1))
    assert result.name == "Mathematics"


def test_create_uses_injected_deterministic_id_factory():
    service, write_port = _service(resource_id_factory=lambda: "resource_deterministic_test_id")
    result = service.create(_SCHOOL, _YEAR, ResourceFields(name="Music Room", capacity=1))
    assert result.id == "resource_deterministic_test_id"
    assert write_port.create_calls[0] == ("resource_deterministic_test_id", "Music Room", 1)


def test_create_rejected_once_schedule_exists():
    service, _ = _service(active_schedule="anything-non-none")
    with pytest.raises(ConfigurationLockedError):
        service.create(_SCHOOL, _YEAR, ResourceFields(name="Music Room", capacity=1))


# -- UPDATE ---------------------------------------------------------------

def test_update_renames_resource():
    service, write_port = _service()
    result = service.update(_SCHOOL, _YEAR, "gym", ResourceFields(name="Sports Hall", capacity=1))
    assert result.id == "gym"
    assert result.name == "Sports Hall"
    assert write_port.update_calls[0] == ("gym", "Sports Hall", 1)


def test_update_changes_capacity():
    service, write_port = _service()
    result = service.update(_SCHOOL, _YEAR, "gym", ResourceFields(name="Indoor Gym", capacity=2))
    assert result.capacity == 2
    assert write_port.update_calls[0] == ("gym", "Indoor Gym", 2)


def test_update_preserves_natural_id():
    service, _ = _service()
    result = service.update(_SCHOOL, _YEAR, "gym", ResourceFields(name="Renamed", capacity=1))
    assert result.id == "gym"


def test_update_capacity_zero_rejected():
    service, _ = _service()
    with pytest.raises(InvalidResourceError):
        service.update(_SCHOOL, _YEAR, "gym", ResourceFields(name="Indoor Gym", capacity=0))


def test_update_duplicate_other_resource_rejected():
    problem = build_valid_fixture()
    problem = replace(problem, resources=problem.resources + (Resource(id="lab", name="Science Lab", capacity=1),))
    service, _ = _service(problem=problem)
    with pytest.raises(DuplicateResourceError):
        service.update(_SCHOOL, _YEAR, "gym", ResourceFields(name="Science Lab", capacity=1))


def test_update_same_name_as_subject_allowed():
    service, _ = _service()
    result = service.update(_SCHOOL, _YEAR, "gym", ResourceFields(name="Mathematics", capacity=1))
    assert result.name == "Mathematics"


def test_update_same_resource_retaining_own_name_allowed():
    service, write_port = _service()
    result = service.update(_SCHOOL, _YEAR, "gym", ResourceFields(name="Indoor Gym", capacity=1))
    assert result.name == "Indoor Gym"
    assert write_port.update_calls[0] == ("gym", "Indoor Gym", 1)


def test_update_trims_value():
    service, write_port = _service()
    service.update(_SCHOOL, _YEAR, "gym", ResourceFields(name="  Renamed  ", capacity=1))
    assert write_port.update_calls[0] == ("gym", "Renamed", 1)


def test_update_missing_id_rejected():
    service, _ = _service()
    with pytest.raises(ResourceNotFoundError):
        service.update(_SCHOOL, _YEAR, "no-such-resource", ResourceFields(name="X", capacity=1))


def test_update_rejected_once_schedule_exists():
    service, _ = _service(active_schedule="anything-non-none")
    with pytest.raises(ConfigurationLockedError):
        service.update(_SCHOOL, _YEAR, "gym", ResourceFields(name="Renamed", capacity=1))


# -- DELETE -----------------------------------------------------------------

def test_delete_unused_resource_succeeds():
    problem = _problem_with_unused_resource(build_valid_fixture())
    service, write_port = _service(problem=problem)
    service.delete(_SCHOOL, _YEAR, "unused_resource")
    assert write_port.delete_calls == ["unused_resource"]


def test_delete_missing_rejected():
    service, _ = _service()
    with pytest.raises(ResourceNotFoundError):
        service.delete(_SCHOOL, _YEAR, "no-such-resource")


def test_delete_teaching_requirement_blocker():
    # "gym" is required by Sport/Dance TeachingRequirements in the fixture.
    service, _ = _service()
    with pytest.raises(ResourceInUseError) as exc_info:
        service.delete(_SCHOOL, _YEAR, "gym")
    assert exc_info.value.referenced_by == ("TEACHING_REQUIREMENT",)


def test_delete_rejected_once_schedule_exists():
    service, _ = _service(active_schedule="anything-non-none")
    with pytest.raises(ConfigurationLockedError):
        service.delete(_SCHOOL, _YEAR, "gym")


# -- Different AY independence (fixture-level; full cross-AY defense is
#    proven for real against PostgreSQL in test_resource_repository.py) --

def test_create_in_one_problem_does_not_affect_a_different_problem_instance():
    service_a, write_port_a = _service()
    other_problem = _problem_with_unused_resource(build_valid_fixture(), resource_id="unused_resource")
    service_b, write_port_b = _service(problem=other_problem)

    service_a.create(_SCHOOL, _YEAR, ResourceFields(name="Music Room", capacity=1))
    assert write_port_b.create_calls == []
    service_b.delete(_SCHOOL, _YEAR, "unused_resource")
    assert write_port_a.delete_calls == []
