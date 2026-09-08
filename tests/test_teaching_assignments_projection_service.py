"""Pure, DB-free tests for Phase 3C.2b's
`TeachingAssignmentsProjectionService` (`docs/DECISIONS.md` #34-#36).
No database, no FastAPI -- both repository ports are small in-memory
fakes returning real domain/application objects, so every projection
rule (editability classification, canonical WHOLE_CLASS-target mapping,
workload totaling, ordering, name resolution) can be exercised in
isolation, mirroring `tests/test_class_timetable_service.py`'s exact
pattern.

The real-PostgreSQL/HTTP end-to-end proof lives in
`tests_web/test_teaching_assignment_api.py`.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from school_timetable.application.errors import SchedulingProblemNotFoundError
from school_timetable.application.teaching_assignments_projection_service import (
    TeachingAssignmentsProjectionService,
)
from school_timetable.domain.groups import ClassSection, ParticipantGroup, ParticipantGroupRole
from school_timetable.domain.people import Teacher
from school_timetable.domain.problem import SchedulingProblem
from school_timetable.fixtures.valid_fixture import build_valid_fixture

_SCHOOL = "synthetic-school"
_YEAR = "ay-2026"


class _FakeProblemRepository:
    def __init__(self, problem: SchedulingProblem) -> None:
        self._problem = problem

    def load_by_school_and_year(self, school_natural_id: str, academic_year_natural_id: str) -> SchedulingProblem:
        if school_natural_id != self._problem.school.id or academic_year_natural_id != self._problem.academic_year.id:
            raise SchedulingProblemNotFoundError(school_natural_id, academic_year_natural_id)
        return self._problem


class _FakeScheduleRepository:
    def __init__(self, active=None) -> None:
        self._active = active

    def get_active_schedule(self, school_natural_id: str, academic_year_natural_id: str):
        return self._active


def _service(problem: SchedulingProblem | None = None, active=None) -> TeachingAssignmentsProjectionService:
    problem = problem if problem is not None else build_valid_fixture()
    return TeachingAssignmentsProjectionService(_FakeProblemRepository(problem), _FakeScheduleRepository(active))


def _by_id(items, item_id: str):
    return next(i for i in items if i.id == item_id)


# -- assignments: completeness, editability, names, class_sections ---------

def test_projection_contains_every_teaching_requirement():
    problem = build_valid_fixture()
    view = _service(problem).project(_SCHOOL, _YEAR)
    assert len(view.assignments) == len(problem.teaching_requirements)
    assert {a.id for a in view.assignments} == {r.id for r in problem.teaching_requirements}


def test_plain_assignment_is_editable_with_no_reasons():
    view = _service().project(_SCHOOL, _YEAR)
    science_8a = _by_id(view.assignments, "science_8a")
    assert science_8a.editable is True
    assert science_8a.advanced_reasons == ()


def test_advanced_block_policy_assignment_is_not_editable():
    view = _service().project(_SCHOOL, _YEAR)
    math_8a = _by_id(view.assignments, "math_8a")
    assert math_8a.editable is False
    assert "block_policy" in math_8a.advanced_reasons


def test_fixed_placement_makes_otherwise_plain_assignment_non_editable():
    # art_8b is otherwise plain (FLEXIBLE, WHOLE_CLASS, no distribution/
    # time-preference/resource/split) -- only its FixedPlacement
    # (fixed_art_8b) makes it advanced, proving the shared
    # `plain_reasons` predicate is reused verbatim, not reimplemented.
    view = _service().project(_SCHOOL, _YEAR)
    art_8b = _by_id(view.assignments, "art_8b")
    assert art_8b.editable is False
    assert art_8b.advanced_reasons == ("fixed_placement",)


def test_teacher_and_activity_labels_resolved():
    view = _service().project(_SCHOOL, _YEAR)
    math_8a = _by_id(view.assignments, "math_8a")
    assert math_8a.teacher_id == "t_math"
    assert math_8a.teacher_name == "Teacher Math"
    assert math_8a.activity_id == "math"
    assert math_8a.activity_name == "Mathematics"


def test_participant_group_name_and_role_resolved():
    view = _service().project(_SCHOOL, _YEAR)
    math_8a = _by_id(view.assignments, "math_8a")
    assert math_8a.participant_group_id == "pg_8a"
    assert math_8a.participant_group_name == "All of 8-A"
    assert math_8a.participant_group_role == "WHOLE_CLASS"

    german_8a = _by_id(view.assignments, "german_8a")
    assert german_8a.participant_group_role == "SUBGROUP"


def test_class_sections_resolved_for_whole_class_and_merged_targets():
    view = _service().project(_SCHOOL, _YEAR)
    math_8a = _by_id(view.assignments, "math_8a")
    assert [(c.id, c.name) for c in math_8a.class_sections] == [("8a", "8-A")]

    merged = _by_id(view.assignments, "history_merged_9a_9b")
    assert [(c.id, c.name) for c in merged.class_sections] == [("9a", "9-A"), ("9b", "9-B")]


# -- teacher workloads -------------------------------------------------

def test_teacher_workload_sums_every_requirement_type_for_that_teacher():
    # t_history has one plain WHOLE_CLASS row (history_8a, 9), one
    # advanced WHOLE_CLASS row (history_8b, 13), and one MERGED_CLASSES
    # row (history_merged_9a_9b, 1) -- the total must include all three.
    view = _service().project(_SCHOOL, _YEAR)
    history_workload = next(w for w in view.teacher_workloads if w.teacher_id == "t_history")
    assert history_workload.total_weekly_periods == 9 + 13 + 1
    assert history_workload.teacher_name == "Teacher History"


def test_zero_workload_teacher_appears_with_zero_total():
    problem = build_valid_fixture()
    problem = replace(problem, teachers=problem.teachers + (Teacher(id="t_zero", name="Teacher Zero"),))
    view = _service(problem).project(_SCHOOL, _YEAR)
    zero_workload = next(w for w in view.teacher_workloads if w.teacher_id == "t_zero")
    assert zero_workload.total_weekly_periods == 0
    assert zero_workload.teacher_name == "Teacher Zero"


def test_every_teacher_appears_exactly_once_in_workloads():
    problem = build_valid_fixture()
    view = _service(problem).project(_SCHOOL, _YEAR)
    assert [w.teacher_id for w in view.teacher_workloads] == [t.id for t in problem.teachers]


# -- reference-data option lists ----------------------------------------

def test_teachers_and_activities_option_lists_preserve_full_content_and_order():
    problem = build_valid_fixture()
    view = _service(problem).project(_SCHOOL, _YEAR)
    assert [(t.id, t.name) for t in view.teachers] == [(t.id, t.name) for t in problem.teachers]
    assert [(a.id, a.name) for a in view.activities] == [(a.id, a.name) for a in problem.activities]


def test_canonical_whole_class_target_mapping_correct():
    view = _service().project(_SCHOOL, _YEAR)
    by_class = {t.class_section_id: t for t in view.whole_class_targets}
    assert by_class["8a"].participant_group_id == "pg_8a"
    assert by_class["8a"].participant_group_name == "All of 8-A"
    assert by_class["8a"].class_section_name == "8-A"
    assert set(by_class) == {"8a", "8b", "9a", "9b"}


def test_subgroup_and_merged_groups_never_appear_as_whole_class_targets():
    view = _service().project(_SCHOOL, _YEAR)
    target_group_ids = {t.participant_group_id for t in view.whole_class_targets}
    assert "pg_8a_german" not in target_group_ids
    assert "pg_8a_russian" not in target_group_ids
    assert "pg_9a_9b_merged" not in target_group_ids


def test_whole_class_targets_ordered_by_class_section_order():
    problem = build_valid_fixture()
    view = _service(problem).project(_SCHOOL, _YEAR)
    assert [t.class_section_id for t in view.whole_class_targets] == [c.id for c in problem.class_sections]


def test_missing_canonical_whole_class_group_is_omitted_not_guessed():
    problem = build_valid_fixture()
    # A ClassSection with zero WHOLE_CLASS groups -- the Decision #33
    # invariant would be broken by real preflight, but this projection
    # must fail soft (omit), never guess a substitute.
    problem = replace(problem, class_sections=problem.class_sections + (ClassSection(id="10a", name="10-A"),))
    view = _service(problem).project(_SCHOOL, _YEAR)
    assert "10a" not in {t.class_section_id for t in view.whole_class_targets}


def test_ambiguous_canonical_whole_class_group_is_omitted_not_guessed():
    problem = build_valid_fixture()
    # Two WHOLE_CLASS groups both claiming "8a" -- also a broken
    # Decision #33 invariant; still must be omitted, never guessed.
    problem = replace(
        problem,
        participant_groups=problem.participant_groups + (
            ParticipantGroup(
                id="pg_8a_duplicate", name="Also All of 8-A", class_sections=("8a",),
                role=ParticipantGroupRole.WHOLE_CLASS,
            ),
        ),
    )
    view = _service(problem).project(_SCHOOL, _YEAR)
    assert "8a" not in {t.class_section_id for t in view.whole_class_targets}


# -- ordering -------------------------------------------------------------

def test_assignments_ordered_by_teaching_requirement_tuple_order():
    problem = build_valid_fixture()
    view = _service(problem).project(_SCHOOL, _YEAR)
    assert [a.id for a in view.assignments] == [r.id for r in problem.teaching_requirements]


# -- configuration_locked --------------------------------------------------

def test_configuration_locked_false_when_no_active_schedule():
    view = _service(active=None).project(_SCHOOL, _YEAR)
    assert view.configuration_locked is False


def test_configuration_locked_true_when_active_schedule_exists():
    view = _service(active="anything-non-none").project(_SCHOOL, _YEAR)
    assert view.configuration_locked is True


def test_configuration_locked_true_still_returns_full_assignment_list():
    problem = build_valid_fixture()
    view = _service(problem, active="anything-non-none").project(_SCHOOL, _YEAR)
    assert view.configuration_locked is True
    assert len(view.assignments) == len(problem.teaching_requirements)


# -- unknown school/year ----------------------------------------------------

def test_unknown_school_year_propagates_scheduling_problem_not_found():
    service = _service()
    with pytest.raises(SchedulingProblemNotFoundError):
        service.project("no-such-school", "no-such-year")
