"""Pure orchestration/validation tests for Real-School Setup MVP Slice
C's `ClassSectionService`. No database, no PostgreSQL -- all three
repository ports are small in-memory fakes; real-PostgreSQL
persistence/concurrency proof lives in
`tests_web/test_class_section_repository.py`.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from school_timetable.application.class_section_models import ClassSectionFields
from school_timetable.application.class_section_rules import CanonicalWholeClassGroupInvariantError
from school_timetable.application.class_section_service import ClassSectionService
from school_timetable.application.errors import (
    ClassSectionInUseError,
    ClassSectionNotFoundError,
    ConfigurationLockedError,
    DuplicateClassError,
    InvalidClassError,
)
from school_timetable.domain.blocks import ReservedBlock
from school_timetable.domain.calendar import TimeSlot
from school_timetable.domain.groups import ClassSection, ParticipantGroup, ParticipantGroupRole
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
    PostgreSQL in `tests_web/test_class_section_repository.py`; this
    fake exists only to exercise `ClassSectionService`'s own
    orchestration and error propagation."""

    def __init__(self, problem):
        self._problem = problem
        self.create_calls: list[tuple] = []
        self.update_calls: list[tuple] = []
        self.delete_calls: list[str] = []

    def create(self, school, year, class_natural_id, group_natural_id, name, validate):
        validate(self._problem)
        self.create_calls.append((class_natural_id, group_natural_id, name))

    def update(self, school, year, class_natural_id, name, validate):
        validate(self._problem)
        self.update_calls.append((class_natural_id, name))

    def delete(self, school, year, class_natural_id, validate):
        validate(self._problem)
        self.delete_calls.append(class_natural_id)


def _service(problem=None, active_schedule=None, class_id_factory=None, group_id_factory=None):
    problem = problem if problem is not None else build_valid_fixture()
    write_port = _FakeWritePort(problem)
    kwargs = {}
    if class_id_factory is not None:
        kwargs["class_id_factory"] = class_id_factory
    if group_id_factory is not None:
        kwargs["group_id_factory"] = group_id_factory
    service = ClassSectionService(
        _FakeProblemRepository(problem), write_port, _FakeScheduleRepository(active=active_schedule), **kwargs,
    )
    return service, write_port


# -- CREATE ---------------------------------------------------------------

def test_create_normal_class_succeeds():
    service, write_port = _service()
    result = service.create(_SCHOOL, _YEAR, ClassSectionFields(name="10-A"))
    assert result.name == "10-A"
    assert write_port.create_calls[0][0] == result.id
    assert write_port.create_calls[0][2] == "10-A"


def test_create_trims_outer_whitespace():
    service, write_port = _service()
    result = service.create(_SCHOOL, _YEAR, ClassSectionFields(name="  10-A  "))
    assert result.name == "10-A"
    assert write_port.create_calls[0][2] == "10-A"


def test_create_blank_name_rejected():
    service, _ = _service()
    with pytest.raises(InvalidClassError) as exc_info:
        service.create(_SCHOOL, _YEAR, ClassSectionFields(name="   "))
    assert any(e.code == "BLANK_CLASS_NAME" for e in exc_info.value.validation_errors)


def test_create_accepts_georgian_unicode_name():
    service, _ = _service()
    result = service.create(_SCHOOL, _YEAR, ClassSectionFields(name="მე-7 ა"))
    assert result.name == "მე-7 ა"


def test_create_accepts_hyphen_space_name():
    service, _ = _service()
    result = service.create(_SCHOOL, _YEAR, ClassSectionFields(name="Grade 10 A"))
    assert result.name == "Grade 10 A"


def test_create_duplicate_trimmed_exact_name_rejected():
    # "8-A" already exists in the fixture.
    service, _ = _service()
    with pytest.raises(DuplicateClassError):
        service.create(_SCHOOL, _YEAR, ClassSectionFields(name="8-A"))
    with pytest.raises(DuplicateClassError):
        service.create(_SCHOOL, _YEAR, ClassSectionFields(name="  8-A  "))


def test_create_case_sensitive_distinct_name_allowed():
    # "8-a" (lowercase) is distinct from "8-A" for this MVP.
    service, _ = _service()
    result = service.create(_SCHOOL, _YEAR, ClassSectionFields(name="8-a"))
    assert result.name == "8-a"


def test_create_generates_server_side_class_and_group_ids():
    service, write_port = _service()
    result = service.create(_SCHOOL, _YEAR, ClassSectionFields(name="10-A"))
    class_natural_id, group_natural_id, _name = write_port.create_calls[0]
    assert result.id == class_natural_id
    assert class_natural_id.startswith("class_")
    assert len(class_natural_id) == len("class_") + 32
    assert group_natural_id.startswith("group_")
    assert len(group_natural_id) == len("group_") + 32
    assert class_natural_id != group_natural_id


def test_create_uses_injected_deterministic_id_factories():
    service, write_port = _service(
        class_id_factory=lambda: "class_deterministic_test_id",
        group_id_factory=lambda: "group_deterministic_test_id",
    )
    result = service.create(_SCHOOL, _YEAR, ClassSectionFields(name="10-A"))
    assert result.id == "class_deterministic_test_id"
    assert write_port.create_calls[0] == ("class_deterministic_test_id", "group_deterministic_test_id", "10-A")


# -- UPDATE ---------------------------------------------------------------

def test_update_renames_class():
    service, write_port = _service()
    result = service.update(_SCHOOL, _YEAR, "8a", ClassSectionFields(name="8-Z"))
    assert result.id == "8a"
    assert result.name == "8-Z"
    assert write_port.update_calls[0] == ("8a", "8-Z")


def test_update_preserves_class_natural_id():
    service, _ = _service()
    result = service.update(_SCHOOL, _YEAR, "8a", ClassSectionFields(name="Renamed"))
    assert result.id == "8a"


def test_update_trims_value():
    service, write_port = _service()
    service.update(_SCHOOL, _YEAR, "8a", ClassSectionFields(name="  8-Z  "))
    assert write_port.update_calls[0] == ("8a", "8-Z")


def test_update_duplicate_against_other_class_rejected():
    service, _ = _service()
    with pytest.raises(DuplicateClassError):
        service.update(_SCHOOL, _YEAR, "8a", ClassSectionFields(name="8-B"))


def test_update_same_class_retaining_its_own_name_allowed():
    service, write_port = _service()
    result = service.update(_SCHOOL, _YEAR, "8a", ClassSectionFields(name="8-A"))
    assert result.name == "8-A"
    assert write_port.update_calls[0] == ("8a", "8-A")


def test_update_missing_class_rejected():
    service, _ = _service()
    with pytest.raises(ClassSectionNotFoundError):
        service.update(_SCHOOL, _YEAR, "no-such-class", ClassSectionFields(name="X"))


def test_update_malformed_zero_canonical_invariant_raises_internal_defect():
    problem = build_valid_fixture()
    # Remove pg_8a (the canonical WHOLE_CLASS group for "8a") without
    # removing the class itself -- a deliberately corrupted problem.
    broken = replace(
        problem, participant_groups=tuple(g for g in problem.participant_groups if g.id != "pg_8a"),
    )
    service, _ = _service(problem=broken)
    with pytest.raises(CanonicalWholeClassGroupInvariantError):
        service.update(_SCHOOL, _YEAR, "8a", ClassSectionFields(name="Renamed"))


def test_update_malformed_duplicate_canonical_invariant_raises_internal_defect():
    problem = build_valid_fixture()
    duplicate_group = ParticipantGroup(
        id="pg_8a_duplicate", name="Duplicate", class_sections=("8a",), role=ParticipantGroupRole.WHOLE_CLASS,
    )
    broken = replace(problem, participant_groups=problem.participant_groups + (duplicate_group,))
    service, _ = _service(problem=broken)
    with pytest.raises(CanonicalWholeClassGroupInvariantError):
        service.update(_SCHOOL, _YEAR, "8a", ClassSectionFields(name="Renamed"))


# -- DELETE -----------------------------------------------------------------

def _problem_with_unused_class(problem, class_id="zz", group_id="pg_zz"):
    return replace(
        problem,
        class_sections=problem.class_sections + (ClassSection(id=class_id, name="Unused"),),
        participant_groups=problem.participant_groups + (
            ParticipantGroup(id=group_id, name="Unused", class_sections=(class_id,), role=ParticipantGroupRole.WHOLE_CLASS),
        ),
    )


def test_delete_unused_class_succeeds():
    problem = _problem_with_unused_class(build_valid_fixture())
    service, write_port = _service(problem=problem)
    service.delete(_SCHOOL, _YEAR, "zz")
    assert write_port.delete_calls == ["zz"]


def test_delete_missing_class_rejected():
    service, _ = _service()
    with pytest.raises(ClassSectionNotFoundError):
        service.delete(_SCHOOL, _YEAR, "no-such-class")


def test_delete_teaching_requirement_blocker():
    # Every fixture ClassSection also has a ReservedBlock and/or
    # SUBGROUP/MERGED_CLASSES reference, so isolate this one blocker
    # kind with a purpose-built unused class + a single extra
    # TeachingRequirement targeting its canonical group.
    problem = _problem_with_unused_class(build_valid_fixture())
    problem = replace(
        problem,
        teaching_requirements=problem.teaching_requirements + (
            replace(problem.teaching_requirements[0], id="__extra__", participant_group_id="pg_zz"),
        ),
    )
    service, _ = _service(problem=problem)
    with pytest.raises(ClassSectionInUseError) as exc_info:
        service.delete(_SCHOOL, _YEAR, "zz")
    assert exc_info.value.referenced_by == ("TEACHING_REQUIREMENT",)


def test_delete_reserved_block_blocker():
    problem = _problem_with_unused_class(build_valid_fixture())
    problem = replace(
        problem,
        reserved_blocks=problem.reserved_blocks + (
            ReservedBlock(
                id="club_extra", name="Extra Club", activity_id="club_chess",
                class_sections=("zz",), slots=(TimeSlot("mon", "p1"),),
            ),
        ),
    )
    service, _ = _service(problem=problem)
    with pytest.raises(ClassSectionInUseError) as exc_info:
        service.delete(_SCHOOL, _YEAR, "zz")
    assert exc_info.value.referenced_by == ("RESERVED_BLOCK",)


def test_delete_subgroup_blocker():
    problem = _problem_with_unused_class(build_valid_fixture())
    problem = replace(
        problem,
        participant_groups=problem.participant_groups + (
            ParticipantGroup(id="pg_zz_sub", name="ZZ Sub", class_sections=("zz",), role=ParticipantGroupRole.SUBGROUP),
        ),
    )
    service, _ = _service(problem=problem)
    with pytest.raises(ClassSectionInUseError) as exc_info:
        service.delete(_SCHOOL, _YEAR, "zz")
    assert exc_info.value.referenced_by == ("SUBGROUP",)


def test_delete_merged_classes_blocker():
    problem = _problem_with_unused_class(build_valid_fixture())
    problem = replace(
        problem,
        participant_groups=problem.participant_groups + (
            ParticipantGroup(
                id="pg_zz_merged", name="ZZ Merged", class_sections=("zz", "8a"),
                role=ParticipantGroupRole.MERGED_CLASSES,
            ),
        ),
    )
    service, _ = _service(problem=problem)
    with pytest.raises(ClassSectionInUseError) as exc_info:
        service.delete(_SCHOOL, _YEAR, "zz")
    assert exc_info.value.referenced_by == ("MERGED_CLASSES",)


def test_delete_combined_blockers_deterministic_order():
    problem = _problem_with_unused_class(build_valid_fixture())
    problem = replace(
        problem,
        teaching_requirements=problem.teaching_requirements + (
            replace(problem.teaching_requirements[0], id="__extra__", participant_group_id="pg_zz"),
        ),
        reserved_blocks=problem.reserved_blocks + (
            ReservedBlock(
                id="club_extra", name="Extra Club", activity_id="club_chess",
                class_sections=("zz",), slots=(TimeSlot("mon", "p1"),),
            ),
        ),
        participant_groups=problem.participant_groups + (
            ParticipantGroup(id="pg_zz_sub", name="ZZ Sub", class_sections=("zz",), role=ParticipantGroupRole.SUBGROUP),
            ParticipantGroup(
                id="pg_zz_merged", name="ZZ Merged", class_sections=("zz", "8a"),
                role=ParticipantGroupRole.MERGED_CLASSES,
            ),
        ),
    )
    service, _ = _service(problem=problem)
    with pytest.raises(ClassSectionInUseError) as exc_info:
        service.delete(_SCHOOL, _YEAR, "zz")
    assert exc_info.value.referenced_by == (
        "TEACHING_REQUIREMENT", "RESERVED_BLOCK", "SUBGROUP", "MERGED_CLASSES",
    )


def test_delete_malformed_canonical_invariant_raises_internal_defect():
    problem = build_valid_fixture()
    broken = replace(
        problem, participant_groups=tuple(g for g in problem.participant_groups if g.id != "pg_8a"),
    )
    service, _ = _service(problem=broken)
    with pytest.raises(CanonicalWholeClassGroupInvariantError):
        service.delete(_SCHOOL, _YEAR, "8a")


# -- LOCK ---------------------------------------------------------------

def test_create_rejected_once_schedule_exists():
    service, _ = _service(active_schedule="anything-non-none")
    with pytest.raises(ConfigurationLockedError):
        service.create(_SCHOOL, _YEAR, ClassSectionFields(name="10-A"))


def test_update_rejected_once_schedule_exists():
    service, _ = _service(active_schedule="anything-non-none")
    with pytest.raises(ConfigurationLockedError):
        service.update(_SCHOOL, _YEAR, "8a", ClassSectionFields(name="Renamed"))


def test_delete_rejected_once_schedule_exists():
    service, _ = _service(active_schedule="anything-non-none")
    with pytest.raises(ConfigurationLockedError):
        service.delete(_SCHOOL, _YEAR, "8a")
