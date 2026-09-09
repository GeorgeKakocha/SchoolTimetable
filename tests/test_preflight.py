from school_timetable.domain.activities import Activity
from school_timetable.domain.blocks import FixedPlacement
from school_timetable.domain.calendar import TimeSlot, AcademicYear
from school_timetable.domain.groups import ClassSection, ParticipantGroup, ParticipantGroupRole
from school_timetable.domain.people import AvailabilityStatus, Teacher, TeacherAvailability
from school_timetable.domain.problem import SchedulingProblem
from school_timetable.domain.requirements import (
    BlockPolicyMode,
    LessonBlockPolicy,
    TeachingRequirement,
)
from school_timetable.domain.resources import ResourceRequirement
from school_timetable.domain.school import School
from school_timetable.fixtures.common import build_days, build_periods
from school_timetable.fixtures.impossible_fixture import build_impossible_fixture
from school_timetable.fixtures.valid_fixture import build_valid_fixture
from school_timetable.validation.preflight import run_preflight

FLEXIBLE = LessonBlockPolicy(BlockPolicyMode.FLEXIBLE)


def _base_problem(**overrides):
    days = build_days()
    periods = build_periods()
    defaults = dict(
        school=School(id="s", name="S"),
        academic_year=AcademicYear(id="ay", label="AY"),
        days=days,
        periods=periods,
        teachers=(Teacher(id="t1", first_name="T1", last_name=""),),
        class_sections=(ClassSection(id="c1", name="C1"),),
        participant_groups=(ParticipantGroup(id="pg1", name="PG1", class_sections=("c1",), role=ParticipantGroupRole.WHOLE_CLASS),),
        activities=(Activity(id="a1", name="A1"),),
        teaching_requirements=(),
    )
    defaults.update(overrides)
    return SchedulingProblem(**defaults)


def test_valid_fixture_has_no_preflight_errors():
    assert run_preflight(build_valid_fixture()) == []


def test_impossible_fixture_flags_teacher_overload():
    errors = run_preflight(build_impossible_fixture())
    codes = {e.code for e in errors}
    assert "TEACHER_OVERLOADED" in codes


def test_unknown_participant_group_reference():
    problem = _base_problem(
        teaching_requirements=(
            TeachingRequirement(
                id="r1", teacher_id="t1", activity_id="a1",
                participant_group_id="does-not-exist", weekly_periods=1, block_policy=FLEXIBLE,
            ),
        ),
    )
    codes = {e.code for e in run_preflight(problem)}
    assert "UNKNOWN_PARTICIPANT_GROUP" in codes


def test_unknown_resource_reference():
    problem = _base_problem(
        teaching_requirements=(
            TeachingRequirement(
                id="r1", teacher_id="t1", activity_id="a1", participant_group_id="pg1",
                weekly_periods=1, block_policy=FLEXIBLE,
                resource_requirement=ResourceRequirement(resource_id="no-such-resource"),
            ),
        ),
    )
    codes = {e.code for e in run_preflight(problem)}
    assert "UNKNOWN_RESOURCE" in codes


def test_block_pattern_total_mismatch():
    problem = _base_problem(
        teaching_requirements=(
            TeachingRequirement(
                id="r1", teacher_id="t1", activity_id="a1", participant_group_id="pg1",
                weekly_periods=5,
                block_policy=LessonBlockPolicy(BlockPolicyMode.REQUIRED, block_sizes=(2, 1, 1)),
            ),
        ),
    )
    codes = {e.code for e in run_preflight(problem)}
    assert "BLOCK_PATTERN_TOTAL_MISMATCH" in codes


def test_preferred_unsupported_block_size_is_rejected():
    # Phase 2A generalized REQUIRED patterns, but PREFERRED intentionally
    # keeps the Phase-1 restriction (at most one size-2 block, rest
    # singles) -- see docs/DECISIONS.md. A size-3 block is still rejected
    # here specifically because the policy is PREFERRED, not REQUIRED.
    problem = _base_problem(
        teaching_requirements=(
            TeachingRequirement(
                id="r1", teacher_id="t1", activity_id="a1", participant_group_id="pg1",
                weekly_periods=3,
                block_policy=LessonBlockPolicy(BlockPolicyMode.PREFERRED, block_sizes=(3,)),
            ),
        ),
    )
    codes = {e.code for e in run_preflight(problem)}
    assert "UNSUPPORTED_BLOCK_SIZE" in codes


def test_preferred_more_than_one_double_is_rejected():
    problem = _base_problem(
        teaching_requirements=(
            TeachingRequirement(
                id="r1", teacher_id="t1", activity_id="a1", participant_group_id="pg1",
                weekly_periods=4,
                block_policy=LessonBlockPolicy(BlockPolicyMode.PREFERRED, block_sizes=(2, 2)),
            ),
        ),
    )
    codes = {e.code for e in run_preflight(problem)}
    assert "UNSUPPORTED_BLOCK_SIZE" in codes


def test_required_generic_block_size_of_three_is_now_accepted():
    # Phase 2A: REQUIRED is no longer limited to singles + one double.
    # A lone size-3 block is a legitimate pattern as long as it is
    # placeable and fits within max_periods_per_day (both true here).
    problem = _base_problem(
        teaching_requirements=(
            TeachingRequirement(
                id="r1", teacher_id="t1", activity_id="a1", participant_group_id="pg1",
                weekly_periods=3,
                block_policy=LessonBlockPolicy(BlockPolicyMode.REQUIRED, block_sizes=(3,)),
            ),
        ),
    )
    codes = {e.code for e in run_preflight(problem)}
    assert "UNSUPPORTED_BLOCK_SIZE" not in codes
    assert "BLOCK_LENGTH_UNPLACEABLE" not in codes


def test_fixed_placement_on_unavailable_slot_is_rejected():
    problem = _base_problem(
        teaching_requirements=(
            TeachingRequirement(
                id="r1", teacher_id="t1", activity_id="a1", participant_group_id="pg1",
                weekly_periods=1, block_policy=FLEXIBLE,
            ),
        ),
        teacher_availabilities=(
            TeacherAvailability("t1", "mon", "p1", AvailabilityStatus.UNAVAILABLE),
        ),
        fixed_placements=(
            FixedPlacement(id="fp1", requirement_id="r1", slot=TimeSlot("mon", "p1")),
        ),
    )
    codes = {e.code for e in run_preflight(problem)}
    assert "FIXED_PLACEMENT_TEACHER_UNAVAILABLE" in codes


def test_class_occupancy_mismatch_detected():
    # Only 1 weekly period declared for a class that has 40 instructional
    # slots -- nowhere near full occupancy.
    problem = _base_problem(
        teaching_requirements=(
            TeachingRequirement(
                id="r1", teacher_id="t1", activity_id="a1", participant_group_id="pg1",
                weekly_periods=1, block_policy=FLEXIBLE,
            ),
        ),
    )
    codes = {e.code for e in run_preflight(problem)}
    assert "CLASS_OCCUPANCY_MISMATCH" in codes


def test_split_group_weekly_periods_mismatch_detected():
    problem = _base_problem(
        participant_groups=(
            ParticipantGroup(id="pg1", name="PG1", class_sections=("c1",), role=ParticipantGroupRole.SUBGROUP),
            ParticipantGroup(id="pg2", name="PG2", class_sections=("c1",), role=ParticipantGroupRole.SUBGROUP),
        ),
        teachers=(Teacher(id="t1", first_name="T1", last_name=""), Teacher(id="t2", first_name="T2", last_name="")),
        teaching_requirements=(
            TeachingRequirement(
                id="r1", teacher_id="t1", activity_id="a1", participant_group_id="pg1",
                weekly_periods=3, block_policy=FLEXIBLE, split_group_id="split1",
            ),
            TeachingRequirement(
                id="r2", teacher_id="t2", activity_id="a1", participant_group_id="pg2",
                weekly_periods=4, block_policy=FLEXIBLE, split_group_id="split1",
            ),
        ),
    )
    codes = {e.code for e in run_preflight(problem)}
    assert "SPLIT_GROUP_WEEKLY_PERIODS_MISMATCH" in codes


def test_participant_group_role_enum_has_the_three_authoritative_values():
    assert ParticipantGroupRole.WHOLE_CLASS.value == "WHOLE_CLASS"
    assert ParticipantGroupRole.SUBGROUP.value == "SUBGROUP"
    assert ParticipantGroupRole.MERGED_CLASSES.value == "MERGED_CLASSES"


def test_whole_class_group_with_two_class_sections_is_rejected():
    problem = _base_problem(
        class_sections=(ClassSection(id="c1", name="C1"), ClassSection(id="c2", name="C2")),
        participant_groups=(
            ParticipantGroup(
                id="pg1", name="PG1", class_sections=("c1", "c2"), role=ParticipantGroupRole.WHOLE_CLASS,
            ),
        ),
    )
    codes = {e.code for e in run_preflight(problem)}
    assert "PARTICIPANT_GROUP_ROLE_CARDINALITY_MISMATCH" in codes


def test_subgroup_with_two_class_sections_is_rejected():
    problem = _base_problem(
        class_sections=(ClassSection(id="c1", name="C1"), ClassSection(id="c2", name="C2")),
        participant_groups=(
            ParticipantGroup(
                id="pg1", name="PG1", class_sections=("c1", "c2"), role=ParticipantGroupRole.SUBGROUP,
            ),
        ),
    )
    codes = {e.code for e in run_preflight(problem)}
    assert "PARTICIPANT_GROUP_ROLE_CARDINALITY_MISMATCH" in codes


def test_merged_classes_group_with_only_one_class_section_is_rejected():
    problem = _base_problem(
        participant_groups=(
            ParticipantGroup(
                id="pg1", name="PG1", class_sections=("c1",), role=ParticipantGroupRole.MERGED_CLASSES,
            ),
        ),
    )
    codes = {e.code for e in run_preflight(problem)}
    assert "PARTICIPANT_GROUP_ROLE_CARDINALITY_MISMATCH" in codes


def test_whole_class_group_with_zero_class_sections_is_rejected():
    problem = _base_problem(
        participant_groups=(
            ParticipantGroup(id="pg1", name="PG1", class_sections=(), role=ParticipantGroupRole.WHOLE_CLASS),
        ),
    )
    codes = {e.code for e in run_preflight(problem)}
    assert "PARTICIPANT_GROUP_ROLE_CARDINALITY_MISMATCH" in codes


def test_subgroup_with_zero_class_sections_is_rejected():
    problem = _base_problem(
        participant_groups=(
            ParticipantGroup(id="pg1", name="PG1", class_sections=(), role=ParticipantGroupRole.SUBGROUP),
        ),
    )
    codes = {e.code for e in run_preflight(problem)}
    assert "PARTICIPANT_GROUP_ROLE_CARDINALITY_MISMATCH" in codes


def test_merged_classes_group_with_zero_class_sections_is_rejected():
    problem = _base_problem(
        participant_groups=(
            ParticipantGroup(id="pg1", name="PG1", class_sections=(), role=ParticipantGroupRole.MERGED_CLASSES),
        ),
    )
    codes = {e.code for e in run_preflight(problem)}
    assert "PARTICIPANT_GROUP_ROLE_CARDINALITY_MISMATCH" in codes


def test_merged_classes_group_with_duplicated_same_class_is_rejected_on_both_counts():
    # A directly-constructed SchedulingProblem can list the same
    # ClassSection twice -- the persistence-layer UNIQUE constraint that
    # rules this out doesn't apply here, so preflight must catch it: a
    # duplicated tuple must never let MERGED_CLASSES appear valid merely
    # because raw tuple length is 2 (only 1 *distinct* class here).
    problem = _base_problem(
        participant_groups=(
            ParticipantGroup(
                id="pg1", name="PG1", class_sections=("c1", "c1"), role=ParticipantGroupRole.MERGED_CLASSES,
            ),
        ),
    )
    codes = {e.code for e in run_preflight(problem)}
    assert "PARTICIPANT_GROUP_DUPLICATE_CLASS_SECTION" in codes
    assert "PARTICIPANT_GROUP_ROLE_CARDINALITY_MISMATCH" in codes


def test_whole_class_group_with_duplicated_same_class_is_rejected():
    # Exactly 1 *distinct* class here, so WHOLE_CLASS's own cardinality
    # requirement is technically satisfied -- but the duplicate entry
    # itself is still a structural defect and must be flagged on its
    # own, independent of cardinality.
    problem = _base_problem(
        participant_groups=(
            ParticipantGroup(
                id="pg1", name="PG1", class_sections=("c1", "c1"), role=ParticipantGroupRole.WHOLE_CLASS,
            ),
        ),
    )
    codes = {e.code for e in run_preflight(problem)}
    assert "PARTICIPANT_GROUP_DUPLICATE_CLASS_SECTION" in codes


def test_subgroup_with_duplicated_same_class_is_rejected():
    problem = _base_problem(
        participant_groups=(
            ParticipantGroup(
                id="pg1", name="PG1", class_sections=("c1", "c1"), role=ParticipantGroupRole.SUBGROUP,
            ),
        ),
    )
    codes = {e.code for e in run_preflight(problem)}
    assert "PARTICIPANT_GROUP_DUPLICATE_CLASS_SECTION" in codes


def test_class_section_with_no_whole_class_group_is_rejected():
    problem = _base_problem(
        participant_groups=(
            ParticipantGroup(id="pg1", name="PG1", class_sections=("c1",), role=ParticipantGroupRole.SUBGROUP),
        ),
    )
    codes = {e.code for e in run_preflight(problem)}
    assert "CLASS_SECTION_WHOLE_CLASS_GROUP_COUNT_MISMATCH" in codes


def test_class_section_with_two_whole_class_groups_is_rejected():
    problem = _base_problem(
        participant_groups=(
            ParticipantGroup(id="pg1", name="PG1", class_sections=("c1",), role=ParticipantGroupRole.WHOLE_CLASS),
            ParticipantGroup(id="pg2", name="PG2", class_sections=("c1",), role=ParticipantGroupRole.WHOLE_CLASS),
        ),
    )
    codes = {e.code for e in run_preflight(problem)}
    assert "CLASS_SECTION_WHOLE_CLASS_GROUP_COUNT_MISMATCH" in codes


def test_one_whole_class_group_per_class_section_passes_role_checks():
    # A well-formed mix of all three roles across two classes: each class
    # has exactly one WHOLE_CLASS group, plus a SUBGROUP split pair and a
    # MERGED_CLASSES group spanning both -- none of this should trip the
    # role-cardinality or canonical-WHOLE_CLASS checks.
    problem = _base_problem(
        class_sections=(ClassSection(id="c1", name="C1"), ClassSection(id="c2", name="C2")),
        participant_groups=(
            ParticipantGroup(id="pg1", name="PG1", class_sections=("c1",), role=ParticipantGroupRole.WHOLE_CLASS),
            ParticipantGroup(id="pg2", name="PG2", class_sections=("c2",), role=ParticipantGroupRole.WHOLE_CLASS),
            ParticipantGroup(id="pg1_sub", name="PG1 sub", class_sections=("c1",), role=ParticipantGroupRole.SUBGROUP),
            ParticipantGroup(
                id="pg_merged", name="Merged", class_sections=("c1", "c2"), role=ParticipantGroupRole.MERGED_CLASSES,
            ),
        ),
    )
    codes = {e.code for e in run_preflight(problem)}
    assert "PARTICIPANT_GROUP_ROLE_CARDINALITY_MISMATCH" not in codes
    assert "CLASS_SECTION_WHOLE_CLASS_GROUP_COUNT_MISMATCH" not in codes
