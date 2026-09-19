from __future__ import annotations

from dataclasses import replace

import pytest

from school_timetable.application.errors import (
    ClassSectionNotFoundError, ConfigurationLockedError, DuplicateSubgroupNameError,
    InvalidSynchronizedSplitError, PublicIdCollisionError,
    SameTeacherSynchronizedSplitError, UnknownReferenceError,
)
from school_timetable.application.synchronized_split_models import (
    CreateSynchronizedSplitCommand, SynchronizedSplitBranchFields,
)
from school_timetable.application.synchronized_split_service import SynchronizedSplitService
from school_timetable.domain.activities import Activity, ActivityKind
from school_timetable.domain.groups import ParticipantGroupRole
from school_timetable.domain.requirements import DistributionPolicy
from school_timetable.fixtures.valid_fixture import build_valid_fixture
from school_timetable.validation.preflight import run_preflight


def _command(**changes):
    values = dict(
        school_id="synthetic-school", academic_year_id="ay-2026", class_section_id="9a",
        weekly_periods=2,
        branch_a=SynchronizedSplitBranchFields("Russian subgroup", "t_russian", "russian"),
        branch_b=SynchronizedSplitBranchFields("German subgroup", "t_german", "german"),
    )
    values.update(changes)
    return CreateSynchronizedSplitCommand(**values)


class _Problems:
    def __init__(self, problem): self.problem = problem
    def load_by_school_and_year(self, school, year): return self.problem


class _Writes:
    def __init__(self, problem, locked=False):
        self.problem, self.locked, self.calls = problem, locked, []
    def create(self, command, public_ids, validate):
        if self.locked:
            raise ConfigurationLockedError(command.school_id, command.academic_year_id)
        validate(self.problem)
        self.calls.append((command, public_ids))


def _service(problem=None, locked=False, ids=None):
    problem = problem or build_valid_fixture()
    writes = _Writes(problem, locked)
    generated = iter(ids or ("split_test", "group_a", "group_b", "req_a", "req_b"))
    return SynchronizedSplitService(_Problems(problem), writes, lambda prefix: next(generated)), writes


def test_create_returns_complete_public_aggregate_and_normalizes_unicode_names():
    service, writes = _service()
    result = service.create(_command(
        branch_a=SynchronizedSplitBranchFields("  რუსული ჯგუფი  ", "t_russian", "russian"),
    ))
    assert result.split_group_id == "split_test"
    assert result.class_section_id == "9a" and result.weekly_periods == 2
    assert result.branch_a.participant_group_name == "რუსული ჯგუფი"
    assert result.branch_a.participant_group_id == "group_a"
    assert result.branch_a.requirement_id == "req_a"
    assert result.branch_b.participant_group_id == "group_b"
    assert result.branch_b.requirement_id == "req_b"
    assert len(writes.calls) == 1


def test_generated_ids_are_opaque_uuid_style_and_not_surrogate_ids():
    problem = build_valid_fixture(); writes = _Writes(problem)
    result = SynchronizedSplitService(_Problems(problem), writes).create(_command())
    for value, prefix in ((result.split_group_id, "split_"),
                          (result.branch_a.participant_group_id, "group_"),
                          (result.branch_b.participant_group_id, "group_"),
                          (result.branch_a.requirement_id, "req_"),
                          (result.branch_b.requirement_id, "req_")):
        assert value.startswith(prefix) and len(value) == len(prefix) + 32
        int(value.removeprefix(prefix), 16)


@pytest.mark.parametrize("weekly", [0, -1])
def test_invalid_weekly_periods_rejected(weekly):
    with pytest.raises(InvalidSynchronizedSplitError): _service()[0].create(_command(weekly_periods=weekly))


@pytest.mark.parametrize("name", ["", "   "])
def test_blank_subgroup_name_rejected(name):
    with pytest.raises(InvalidSynchronizedSplitError):
        _service()[0].create(_command(branch_a=SynchronizedSplitBranchFields(name, "t_russian", "russian")))


def test_duplicate_normalized_subgroup_names_rejected():
    with pytest.raises(DuplicateSubgroupNameError):
        _service()[0].create(_command(
            branch_a=SynchronizedSplitBranchFields(" Same ", "t_russian", "russian"),
            branch_b=SynchronizedSplitBranchFields("Same", "t_german", "german"),
        ))


def test_same_teacher_rejected_early():
    with pytest.raises(SameTeacherSynchronizedSplitError):
        _service()[0].create(_command(
            branch_b=SynchronizedSplitBranchFields("German subgroup", "t_russian", "german"),
        ))


def test_unknown_class_rejected():
    with pytest.raises(ClassSectionNotFoundError): _service()[0].create(_command(class_section_id="missing"))


@pytest.mark.parametrize("branch", [
    SynchronizedSplitBranchFields("Russian subgroup", "missing", "russian"),
    SynchronizedSplitBranchFields("Russian subgroup", "t_russian", "missing"),
])
def test_unknown_branch_reference_rejected(branch):
    with pytest.raises(UnknownReferenceError): _service()[0].create(_command(branch_a=branch))


def test_nonordinary_activity_rejected():
    problem = build_valid_fixture()
    problem = replace(problem, activities=problem.activities + (Activity("club", "Club", ActivityKind.CLUB),))
    with pytest.raises(Exception) as exc:
        _service(problem)[0].create(_command(
            branch_a=SynchronizedSplitBranchFields("Russian subgroup", "t_russian", "club"),
        ))
    assert type(exc.value).__name__ == "NonOrdinaryActivityTargetError"


def test_public_id_collision_rejected_before_write():
    service, writes = _service(ids=("split_test", "same", "same", "req_a", "req_b"))
    with pytest.raises(PublicIdCollisionError): service.create(_command())
    assert writes.calls == []


def test_complete_two_branch_candidate_passes_blocking_preflight():
    result = _service()[0].create(_command())
    assert {warning.code for warning in result.warnings} <= {"CLASS_OCCUPANCY_MISMATCH", "TEACHER_OVERLOADED"}


def test_locked_configuration_fails_without_repository_mutation():
    service, writes = _service(locked=True)
    with pytest.raises(ConfigurationLockedError): service.create(_command())
    assert writes.calls == []


def test_feature_does_not_add_any_one_per_day_policy():
    assert DistributionPolicy().max_periods_per_day is None
    assert DistributionPolicy().min_distinct_days is None
    # Existing fixture itself contains synchronized split branches and remains valid.
    assert run_preflight(build_valid_fixture()) == []
