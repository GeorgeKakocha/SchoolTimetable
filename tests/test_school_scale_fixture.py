"""Fast unit tests for the Phase 2B school-scale fixture generator.

These deliberately avoid invoking CP-SAT at all (that only happens in
``assemble()``, which proves feasibility via a real solve) -- they only
exercise curriculum construction and the independent occupancy
consistency check, so they stay fast enough for the ordinary
``pytest -q`` run. The heavier, solver-invoking scenarios are in
``test_school_scale_integration.py`` under ``@pytest.mark.slow``.
"""
from __future__ import annotations

from school_timetable.fixtures.school_scale.assemble import build_problem_from_curriculum
from school_timetable.fixtures.school_scale.consistency import check_class_occupancy_consistency
from school_timetable.fixtures.school_scale.curriculum import (
    CLASS_IDS,
    MERGED_CIVICS_PAIRS,
    NUM_CLASSES,
    SPLIT_LANGUAGE_CLASSES,
    build_curriculum,
)
from school_timetable.validation.preflight import run_preflight


def test_curriculum_shape_matches_target_scale():
    curriculum = build_curriculum()
    assert len(curriculum.class_sections) == NUM_CLASSES == 15
    assert 30 <= len(curriculum.teachers) <= 40
    assert 15 <= len(curriculum.activities) <= 20
    assert len(curriculum.reserved_blocks) >= 2


def test_curriculum_is_deterministic():
    a = build_curriculum()
    b = build_curriculum()
    assert [r.id for r in a.requirements] == [r.id for r in b.requirements]
    assert [r.weekly_periods for r in a.requirements] == [r.weekly_periods for r in b.requirements]
    assert a.teacher_availabilities == b.teacher_availabilities


def test_curriculum_has_no_duplicate_requirement_ids():
    curriculum = build_curriculum()
    ids = [r.id for r in curriculum.requirements]
    assert len(ids) == len(set(ids))


def test_class_occupancy_consistency_check_passes_for_generated_curriculum():
    curriculum = build_curriculum()
    problem = build_problem_from_curriculum(curriculum)
    report = check_class_occupancy_consistency(problem)
    assert report.passed, report.mismatches


def test_class_occupancy_consistency_check_detects_a_genuine_mismatch():
    """Prove the check actually checks something: corrupt one class's
    load and confirm it is caught."""
    import dataclasses

    curriculum = build_curriculum()
    problem = build_problem_from_curriculum(curriculum)
    # Inflate one requirement's weekly_periods so its class's total not
    # only overshoots the exact target but does so via a value that
    # cannot possibly be a data-entry coincidence.
    bumped = dataclasses.replace(
        problem.teaching_requirements[0], weekly_periods=problem.teaching_requirements[0].weekly_periods + 5
    )
    corrupted = dataclasses.replace(
        problem, teaching_requirements=(bumped,) + problem.teaching_requirements[1:]
    )
    report = check_class_occupancy_consistency(corrupted)
    assert not report.passed
    assert any("expected weekly occupancy" in m for m in report.mismatches)


def test_split_language_classes_have_two_synchronized_branches_each():
    curriculum = build_curriculum()
    for class_id in SPLIT_LANGUAGE_CLASSES:
        branch_reqs = [
            r for r in curriculum.requirements
            if r.split_group_id is not None and r.class_ids == (class_id,)
        ]
        assert len(branch_reqs) == 2, f"{class_id} should have exactly 2 split branches"
        assert branch_reqs[0].split_group_id == branch_reqs[1].split_group_id
        assert {r.activity_id for r in branch_reqs} == {"subject_german", "subject_russian"}


def test_merged_civics_pairs_are_single_requirements_spanning_two_classes():
    curriculum = build_curriculum()
    merged_reqs = [r for r in curriculum.requirements if len(r.class_ids) > 1]
    assert len(merged_reqs) == len(MERGED_CIVICS_PAIRS)
    for req in merged_reqs:
        assert len(req.class_ids) == 2
        assert req.activity_id == "subject_civics"


def test_gym_resource_demand_is_below_capacity_ceiling():
    """Sanity-check the standard curriculum's gym demand is *meaningful*
    (a real bottleneck) but not impossible (unlike the deliberately
    impossible scenario, which doubles it)."""
    curriculum = build_curriculum()
    gym_periods = sum(r.weekly_periods for r in curriculum.requirements if r.resource_id == "gym")
    total_slots = 5 * 8
    assert gym_periods < total_slots
    assert gym_periods > total_slots // 2  # meaningfully loaded, not trivial


def test_tight_curriculum_has_more_unavailability_than_standard():
    standard = build_curriculum(extra_unavailability=False)
    tight = build_curriculum(extra_unavailability=True)
    assert len(tight.teacher_availabilities) > len(standard.teacher_availabilities)


def test_generated_problem_passes_reference_preflight_checks():
    """Every reference (teacher/activity/group/resource/class) must
    resolve -- this is checked independently of occupancy/feasibility."""
    curriculum = build_curriculum()
    problem = build_problem_from_curriculum(curriculum)
    errors = run_preflight(problem)
    reference_error_codes = {
        "UNKNOWN_TEACHER", "UNKNOWN_ACTIVITY", "UNKNOWN_PARTICIPANT_GROUP",
        "UNKNOWN_CLASS_SECTION", "UNKNOWN_RESOURCE", "UNKNOWN_SLOT", "UNKNOWN_REQUIREMENT",
    }
    codes = {e.code for e in errors}
    assert not (codes & reference_error_codes), errors


def test_class_ids_are_synthetic_and_sequential():
    assert CLASS_IDS[0] == "class_01"
    assert CLASS_IDS[-1] == f"class_{NUM_CLASSES:02d}"
    assert all(c.startswith("class_") for c in CLASS_IDS)
