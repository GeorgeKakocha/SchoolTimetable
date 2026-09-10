from school_timetable.domain.activities import Activity, ActivityKind
from school_timetable.domain.blocks import FixedPlacement, ReservedBlock
from school_timetable.domain.calendar import Period, TimeSlot, AcademicYear
from school_timetable.domain.groups import ClassSection, ParticipantGroup, ParticipantGroupRole
from school_timetable.domain.people import AvailabilityStatus, Teacher, TeacherAvailability
from school_timetable.domain.problem import SchedulingProblem
from school_timetable.domain.requirements import (
    BlockPolicyMode,
    LessonBlockPolicy,
    TeachingRequirement,
)
from school_timetable.domain.resources import Resource, ResourceRequirement
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


def test_unknown_resource_reference_unaffected_by_resource_capacity_check():
    """`UNKNOWN_RESOURCE` (a `TeachingRequirement` referencing a missing
    `Resource`) and `INVALID_RESOURCE_CAPACITY` (a `Resource` entity's
    own invalid capacity) are independent invariants -- this repeats
    `test_unknown_resource_reference` with a well-formed `Resource`
    present, proving the new capacity check neither suppresses nor
    duplicates the pre-existing unknown-reference diagnostic."""
    problem = _base_problem(
        resources=(Resource(id="gym", name="Gym", capacity=1),),
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
    assert "INVALID_RESOURCE_CAPACITY" not in codes


def test_resource_capacity_zero_rejected():
    problem = _base_problem(resources=(Resource(id="gym", name="Gym", capacity=0),))
    errors = run_preflight(problem)
    codes = {e.code for e in errors}
    assert "INVALID_RESOURCE_CAPACITY" in codes
    invalid = next(e for e in errors if e.code == "INVALID_RESOURCE_CAPACITY")
    assert invalid.context == {"resource_id": "gym", "capacity": 0}


def test_resource_capacity_negative_rejected():
    problem = _base_problem(resources=(Resource(id="gym", name="Gym", capacity=-3),))
    errors = run_preflight(problem)
    codes = {e.code for e in errors}
    assert "INVALID_RESOURCE_CAPACITY" in codes
    invalid = next(e for e in errors if e.code == "INVALID_RESOURCE_CAPACITY")
    assert invalid.context == {"resource_id": "gym", "capacity": -3}


def test_resource_capacity_one_produces_no_diagnostic():
    problem = _base_problem(resources=(Resource(id="gym", name="Gym", capacity=1),))
    codes = {e.code for e in run_preflight(problem)}
    assert "INVALID_RESOURCE_CAPACITY" not in codes


def test_resource_capacity_greater_than_one_produces_no_diagnostic():
    problem = _base_problem(resources=(Resource(id="gym", name="Gym", capacity=5),))
    codes = {e.code for e in run_preflight(problem)}
    assert "INVALID_RESOURCE_CAPACITY" not in codes


def test_multiple_invalid_resources_produce_one_diagnostic_each_in_problem_order():
    problem = _base_problem(
        resources=(
            Resource(id="r_bad_first", name="Bad First", capacity=0),
            Resource(id="r_good", name="Good", capacity=1),
            Resource(id="r_bad_second", name="Bad Second", capacity=-1),
        ),
    )
    errors = [e for e in run_preflight(problem) if e.code == "INVALID_RESOURCE_CAPACITY"]
    assert [e.context["resource_id"] for e in errors] == ["r_bad_first", "r_bad_second"]


def test_valid_fixture_resource_capacity_produces_no_diagnostic():
    """The fixture's own "Indoor Gym" (capacity=1) must never trip the
    new check -- repeats the whole-fixture zero-errors guarantee
    narrowly for this one code, so a future fixture edit that breaks it
    fails loudly here rather than only via the broader
    `test_valid_fixture_has_no_preflight_errors`."""
    codes = {e.code for e in run_preflight(build_valid_fixture())}
    assert "INVALID_RESOURCE_CAPACITY" not in codes


def test_teaching_requirement_targeting_club_activity_is_rejected():
    # Pre-Slice-D correction (defense-in-depth): a TeachingRequirement
    # may never target a CLUB activity -- CLUB is scheduled via
    # ReservedBlock, never a TeachingRequirement.
    problem = _base_problem(
        activities=(Activity(id="a1", name="A1", kind=ActivityKind.CLUB),),
        teaching_requirements=(
            TeachingRequirement(
                id="r1", teacher_id="t1", activity_id="a1",
                participant_group_id="pg1", weekly_periods=1, block_policy=FLEXIBLE,
            ),
        ),
    )
    codes = {e.code for e in run_preflight(problem)}
    assert "NON_ORDINARY_TEACHING_REQUIREMENT_ACTIVITY" in codes


def test_teaching_requirement_targeting_ordinary_activity_produces_no_kind_diagnostic():
    problem = _base_problem(
        activities=(Activity(id="a1", name="A1", kind=ActivityKind.ORDINARY),),
        teaching_requirements=(
            TeachingRequirement(
                id="r1", teacher_id="t1", activity_id="a1",
                participant_group_id="pg1", weekly_periods=1, block_policy=FLEXIBLE,
            ),
        ),
    )
    codes = {e.code for e in run_preflight(problem)}
    assert "NON_ORDINARY_TEACHING_REQUIREMENT_ACTIVITY" not in codes


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


def test_duplicate_teacher_availability_cell_same_status_detected():
    problem = _base_problem(
        teacher_availabilities=(
            TeacherAvailability("t1", "mon", "p1", AvailabilityStatus.UNAVAILABLE),
            TeacherAvailability("t1", "mon", "p1", AvailabilityStatus.UNAVAILABLE),
        ),
    )
    codes = {e.code for e in run_preflight(problem)}
    assert "DUPLICATE_TEACHER_AVAILABILITY_CELL" in codes


def test_duplicate_teacher_availability_cell_conflicting_status_detected():
    problem = _base_problem(
        teacher_availabilities=(
            TeacherAvailability("t1", "mon", "p1", AvailabilityStatus.UNAVAILABLE),
            TeacherAvailability("t1", "mon", "p1", AvailabilityStatus.PREFER_NOT),
        ),
    )
    codes = {e.code for e in run_preflight(problem)}
    assert "DUPLICATE_TEACHER_AVAILABILITY_CELL" in codes


def test_distinct_teacher_availability_cells_not_flagged_as_duplicate():
    problem = _base_problem(
        teacher_availabilities=(
            TeacherAvailability("t1", "mon", "p1", AvailabilityStatus.UNAVAILABLE),
            TeacherAvailability("t1", "tue", "p2", AvailabilityStatus.PREFER_NOT),
        ),
    )
    codes = {e.code for e in run_preflight(problem)}
    assert "DUPLICATE_TEACHER_AVAILABILITY_CELL" not in codes


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


# == RESERVED ACTIVITIES SLICE A2 ============================================

_TWO_CLASSES = (ClassSection(id="c1", name="C1"), ClassSection(id="c2", name="C2"))
_TWO_TEACHERS = (
    Teacher(id="t1", first_name="T1", last_name=""),
    Teacher(id="t2", first_name="T2", last_name=""),
)
_CLUB_ACTIVITY = Activity(id="club1", name="Club1", kind=ActivityKind.CLUB)
_ORDINARY_ACTIVITY = Activity(id="a1", name="A1", kind=ActivityKind.ORDINARY)
_TWO_CLASS_GROUPS = (
    ParticipantGroup(id="pg1", name="PG1", class_sections=("c1",), role=ParticipantGroupRole.WHOLE_CLASS),
    ParticipantGroup(id="pg2", name="PG2", class_sections=("c2",), role=ParticipantGroupRole.WHOLE_CLASS),
)


def _reserved_block(**overrides):
    defaults = dict(
        id="rb1", name="RB1", activity_id="club1", class_sections=("c1",),
        slots=(TimeSlot("mon", "p1"),), teacher_id=None,
    )
    defaults.update(overrides)
    return ReservedBlock(**defaults)


def _reserved_problem(**overrides):
    defaults = dict(
        class_sections=_TWO_CLASSES,
        participant_groups=_TWO_CLASS_GROUPS,
        teachers=_TWO_TEACHERS,
        activities=(_CLUB_ACTIVITY, _ORDINARY_ACTIVITY),
    )
    defaults.update(overrides)
    return _base_problem(**defaults)


def test_reserved_block_valid_produces_no_new_diagnostics():
    problem = _reserved_problem(reserved_blocks=(_reserved_block(),))
    codes = {e.code for e in run_preflight(problem)}
    assert not (codes & {
        "DUPLICATE_RESERVED_BLOCK_CLASS_SECTION", "DUPLICATE_RESERVED_BLOCK_SLOT",
        "RESERVED_BLOCK_NON_CLUB_ACTIVITY", "RESERVED_BLOCK_NON_INSTRUCTIONAL_SLOT",
        "RESERVED_BLOCK_TEACHER_UNAVAILABLE", "RESERVED_BLOCK_CLASS_SLOT_COLLISION",
        "RESERVED_BLOCK_TEACHER_SLOT_COLLISION",
    })


def test_duplicate_class_within_one_reserved_block_detected():
    problem = _reserved_problem(
        reserved_blocks=(_reserved_block(class_sections=("c1", "c1")),),
    )
    codes = {e.code for e in run_preflight(problem)}
    assert "DUPLICATE_RESERVED_BLOCK_CLASS_SECTION" in codes
    # A same-block duplicate is never mistaken for a cross-block collision.
    assert "RESERVED_BLOCK_CLASS_SLOT_COLLISION" not in codes


def test_duplicate_slot_within_one_reserved_block_detected():
    problem = _reserved_problem(
        reserved_blocks=(_reserved_block(slots=(TimeSlot("mon", "p1"), TimeSlot("mon", "p1"))),),
    )
    codes = {e.code for e in run_preflight(problem)}
    assert "DUPLICATE_RESERVED_BLOCK_SLOT" in codes


def test_reserved_block_unknown_activity_does_not_double_with_non_club():
    problem = _reserved_problem(reserved_blocks=(_reserved_block(activity_id="no-such-activity"),))
    codes = {e.code for e in run_preflight(problem)}
    assert "UNKNOWN_ACTIVITY" in codes
    assert "RESERVED_BLOCK_NON_CLUB_ACTIVITY" not in codes


def test_reserved_block_ordinary_activity_rejected():
    problem = _reserved_problem(reserved_blocks=(_reserved_block(activity_id="a1"),))
    codes = {e.code for e in run_preflight(problem)}
    assert "RESERVED_BLOCK_NON_CLUB_ACTIVITY" in codes


def test_reserved_block_club_activity_produces_no_kind_diagnostic():
    problem = _reserved_problem(reserved_blocks=(_reserved_block(activity_id="club1"),))
    codes = {e.code for e in run_preflight(problem)}
    assert "RESERVED_BLOCK_NON_CLUB_ACTIVITY" not in codes


def test_reserved_block_unknown_slot_does_not_double_with_non_instructional():
    problem = _reserved_problem(reserved_blocks=(_reserved_block(slots=(TimeSlot("mon", "no-such-period"),)),))
    codes = {e.code for e in run_preflight(problem)}
    assert "UNKNOWN_SLOT" in codes
    assert "RESERVED_BLOCK_NON_INSTRUCTIONAL_SLOT" not in codes


def test_reserved_block_non_instructional_slot_rejected():
    non_instructional_periods = build_periods() + (
        Period(id="break", name="Break", index=8, block_id="midday", is_instructional=False),
    )
    problem = _reserved_problem(
        periods=non_instructional_periods,
        reserved_blocks=(_reserved_block(slots=(TimeSlot("mon", "break"),)),),
    )
    codes = {e.code for e in run_preflight(problem)}
    assert "RESERVED_BLOCK_NON_INSTRUCTIONAL_SLOT" in codes


def test_reserved_block_unknown_teacher_does_not_double_with_availability():
    problem = _reserved_problem(reserved_blocks=(_reserved_block(teacher_id="no-such-teacher"),))
    codes = {e.code for e in run_preflight(problem)}
    assert "UNKNOWN_TEACHER" in codes
    assert "RESERVED_BLOCK_TEACHER_UNAVAILABLE" not in codes


def test_reserved_block_teacher_unavailable_rejected():
    problem = _reserved_problem(
        teacher_availabilities=(TeacherAvailability("t1", "mon", "p1", AvailabilityStatus.UNAVAILABLE),),
        reserved_blocks=(_reserved_block(teacher_id="t1"),),
    )
    codes = {e.code for e in run_preflight(problem)}
    assert "RESERVED_BLOCK_TEACHER_UNAVAILABLE" in codes


def test_reserved_block_teacher_prefer_not_produces_no_availability_diagnostic():
    problem = _reserved_problem(
        teacher_availabilities=(TeacherAvailability("t1", "mon", "p1", AvailabilityStatus.PREFER_NOT),),
        reserved_blocks=(_reserved_block(teacher_id="t1"),),
    )
    codes = {e.code for e in run_preflight(problem)}
    assert "RESERVED_BLOCK_TEACHER_UNAVAILABLE" not in codes


def test_reserved_block_class_slot_collision_names_two_different_blocks():
    problem = _reserved_problem(
        reserved_blocks=(
            _reserved_block(id="rb1", class_sections=("c1",), slots=(TimeSlot("mon", "p1"),)),
            _reserved_block(id="rb2", activity_id="club1", class_sections=("c1",), slots=(TimeSlot("mon", "p1"),)),
        ),
    )
    errors = [e for e in run_preflight(problem) if e.code == "RESERVED_BLOCK_CLASS_SLOT_COLLISION"]
    assert len(errors) == 1
    assert errors[0].context["reserved_block_id"] == "rb2"
    assert errors[0].context["conflicting_reserved_block_id"] == "rb1"


def test_reserved_block_teacher_slot_collision_names_two_different_blocks():
    problem = _reserved_problem(
        reserved_blocks=(
            _reserved_block(id="rb1", class_sections=("c1",), slots=(TimeSlot("mon", "p1"),), teacher_id="t1"),
            _reserved_block(id="rb2", class_sections=("c2",), slots=(TimeSlot("mon", "p1"),), teacher_id="t1"),
        ),
    )
    errors = [e for e in run_preflight(problem) if e.code == "RESERVED_BLOCK_TEACHER_SLOT_COLLISION"]
    assert len(errors) == 1
    assert errors[0].context["reserved_block_id"] == "rb2"
    assert errors[0].context["conflicting_reserved_block_id"] == "rb1"


def test_reserved_block_no_mirrored_duplicate_collision_diagnostics():
    problem = _reserved_problem(
        reserved_blocks=(
            _reserved_block(id="rb1", class_sections=("c1",), slots=(TimeSlot("mon", "p1"),)),
            _reserved_block(id="rb2", class_sections=("c1",), slots=(TimeSlot("mon", "p1"),)),
        ),
    )
    errors = [e for e in run_preflight(problem) if e.code == "RESERVED_BLOCK_CLASS_SLOT_COLLISION"]
    # Exactly one directional diagnostic, never a mirrored pair.
    assert len(errors) == 1


def test_reserved_block_different_class_or_different_slot_never_collides():
    problem = _reserved_problem(
        reserved_blocks=(
            _reserved_block(id="rb1", class_sections=("c1",), slots=(TimeSlot("mon", "p1"),)),
            _reserved_block(id="rb2", class_sections=("c2",), slots=(TimeSlot("mon", "p1"),)),
            _reserved_block(id="rb3", class_sections=("c1",), slots=(TimeSlot("mon", "p2"),)),
        ),
    )
    codes = {e.code for e in run_preflight(problem)}
    assert "RESERVED_BLOCK_CLASS_SLOT_COLLISION" not in codes


def test_reserved_block_class_collision_deterministic_order_independent_of_request_order():
    # rb2's own class_sections/slots are supplied out of canonical
    # order; the collision scan must still find the true first owner
    # (rb1, problem/list order) deterministically.
    problem = _reserved_problem(
        reserved_blocks=(
            _reserved_block(id="rb1", class_sections=("c1", "c2"), slots=(TimeSlot("mon", "p1"), TimeSlot("mon", "p2"))),
            _reserved_block(
                id="rb2", class_sections=("c2", "c1"), slots=(TimeSlot("mon", "p2"), TimeSlot("mon", "p1")),
            ),
        ),
    )
    errors = [e for e in run_preflight(problem) if e.code == "RESERVED_BLOCK_CLASS_SLOT_COLLISION"]
    assert all(e.context["conflicting_reserved_block_id"] == "rb1" for e in errors)
    # Each block spans every (class x slot) combination -- (c1,p1),
    # (c1,p2), (c2,p1), (c2,p2) -- all four collide, regardless of the
    # request-order permutation rb2 supplied its own classes/slots in.
    assert len(errors) == 4


def test_reserved_block_teacher_collision_deterministic_order_independent_of_request_order():
    # The teacher-side mirror of the class-side proof above: rb2's own
    # slots are supplied reversed relative to Day.index/Period.index;
    # the collision scan must still find the true first owner (rb1,
    # problem/list order) deterministically, regardless of that
    # request-order permutation.
    problem = _reserved_problem(
        reserved_blocks=(
            _reserved_block(
                id="rb1", class_sections=("c1",), teacher_id="t1",
                slots=(TimeSlot("mon", "p1"), TimeSlot("mon", "p2")),
            ),
            _reserved_block(
                id="rb2", activity_id="club1", class_sections=("c2",), teacher_id="t1",
                slots=(TimeSlot("mon", "p2"), TimeSlot("mon", "p1")),
            ),
        ),
    )
    errors = [e for e in run_preflight(problem) if e.code == "RESERVED_BLOCK_TEACHER_SLOT_COLLISION"]
    assert all(e.context["conflicting_reserved_block_id"] == "rb1" for e in errors)
    # Both requested slots (mon/p1, mon/p2) collide regardless of the
    # order rb2 supplied them in.
    assert len(errors) == 2


# == RESOURCES B2 (Reserved Activity Resource integration) ==================

_ONE_RESOURCE_CAPACITY_1 = (Resource(id="gym", name="Gym", capacity=1),)
_ONE_RESOURCE_CAPACITY_2 = (Resource(id="gym", name="Gym", capacity=2),)


def test_reserved_block_unknown_resource_reference_detected():
    problem = _reserved_problem(reserved_blocks=(_reserved_block(resource_id="no-such-resource"),))
    codes = {e.code for e in run_preflight(problem)}
    assert "UNKNOWN_RESOURCE" in codes
    assert "RESERVED_RESOURCE_CAPACITY_EXCEEDED" not in codes


def test_reserved_block_known_resource_produces_no_unknown_resource_diagnostic():
    problem = _reserved_problem(
        resources=_ONE_RESOURCE_CAPACITY_1, reserved_blocks=(_reserved_block(resource_id="gym"),),
    )
    codes = {e.code for e in run_preflight(problem)}
    assert "UNKNOWN_RESOURCE" not in codes


def test_capacity_one_single_reserved_block_is_valid():
    problem = _reserved_problem(
        resources=_ONE_RESOURCE_CAPACITY_1,
        reserved_blocks=(_reserved_block(id="rb1", class_sections=("c1",), resource_id="gym"),),
    )
    codes = {e.code for e in run_preflight(problem)}
    assert "RESERVED_RESOURCE_CAPACITY_EXCEEDED" not in codes


def test_capacity_one_two_reserved_blocks_same_slot_rejected():
    problem = _reserved_problem(
        resources=_ONE_RESOURCE_CAPACITY_1,
        reserved_blocks=(
            _reserved_block(id="rb1", class_sections=("c1",), slots=(TimeSlot("mon", "p1"),), resource_id="gym"),
            _reserved_block(id="rb2", class_sections=("c2",), slots=(TimeSlot("mon", "p1"),), resource_id="gym"),
        ),
    )
    errors = [e for e in run_preflight(problem) if e.code == "RESERVED_RESOURCE_CAPACITY_EXCEEDED"]
    assert len(errors) == 1
    assert errors[0].context == {
        "resource_id": "gym", "day_id": "mon", "period_id": "p1", "capacity": 1, "reserved_usage": 2,
    }


def test_capacity_two_two_reserved_blocks_same_slot_is_valid():
    problem = _reserved_problem(
        resources=_ONE_RESOURCE_CAPACITY_2,
        reserved_blocks=(
            _reserved_block(id="rb1", class_sections=("c1",), slots=(TimeSlot("mon", "p1"),), resource_id="gym"),
            _reserved_block(id="rb2", class_sections=("c2",), slots=(TimeSlot("mon", "p1"),), resource_id="gym"),
        ),
    )
    codes = {e.code for e in run_preflight(problem)}
    assert "RESERVED_RESOURCE_CAPACITY_EXCEEDED" not in codes


def test_capacity_two_three_reserved_blocks_same_slot_rejected():
    problem = _reserved_problem(
        resources=_ONE_RESOURCE_CAPACITY_2,
        reserved_blocks=(
            _reserved_block(id="rb1", class_sections=("c1",), slots=(TimeSlot("mon", "p1"),), resource_id="gym"),
            _reserved_block(id="rb2", class_sections=("c2",), slots=(TimeSlot("mon", "p1"),), resource_id="gym"),
            _reserved_block(
                id="rb3", activity_id="club1", class_sections=("c1", "c2"),
                slots=(TimeSlot("mon", "p1"),), resource_id="gym",
            ),
        ),
    )
    errors = [e for e in run_preflight(problem) if e.code == "RESERVED_RESOURCE_CAPACITY_EXCEEDED"]
    assert len(errors) == 1
    assert errors[0].context["reserved_usage"] == 3
    assert errors[0].context["capacity"] == 2


def test_multi_class_reserved_block_consumes_only_one_capacity_unit():
    # One ReservedBlock spanning c1+c2 is ONE occupation of "gym" at
    # this slot, never two (once per class) -- capacity=1 must remain
    # satisfied by a single multi-class block alone.
    problem = _reserved_problem(
        resources=_ONE_RESOURCE_CAPACITY_1,
        reserved_blocks=(
            _reserved_block(
                id="rb1", class_sections=("c1", "c2"), slots=(TimeSlot("mon", "p1"),), resource_id="gym",
            ),
        ),
    )
    codes = {e.code for e in run_preflight(problem)}
    assert "RESERVED_RESOURCE_CAPACITY_EXCEEDED" not in codes


def test_different_slots_have_independent_capacity_counts():
    problem = _reserved_problem(
        resources=_ONE_RESOURCE_CAPACITY_1,
        reserved_blocks=(
            _reserved_block(id="rb1", class_sections=("c1",), slots=(TimeSlot("mon", "p1"),), resource_id="gym"),
            _reserved_block(id="rb2", class_sections=("c2",), slots=(TimeSlot("mon", "p2"),), resource_id="gym"),
        ),
    )
    codes = {e.code for e in run_preflight(problem)}
    assert "RESERVED_RESOURCE_CAPACITY_EXCEEDED" not in codes


def test_different_resources_have_independent_capacity_counts():
    problem = _reserved_problem(
        resources=(Resource(id="gym", name="Gym", capacity=1), Resource(id="lab", name="Lab", capacity=1)),
        reserved_blocks=(
            _reserved_block(id="rb1", class_sections=("c1",), slots=(TimeSlot("mon", "p1"),), resource_id="gym"),
            _reserved_block(id="rb2", class_sections=("c2",), slots=(TimeSlot("mon", "p1"),), resource_id="lab"),
        ),
    )
    codes = {e.code for e in run_preflight(problem)}
    assert "RESERVED_RESOURCE_CAPACITY_EXCEEDED" not in codes


def test_reserved_block_without_resource_contributes_zero_usage():
    problem = _reserved_problem(
        resources=_ONE_RESOURCE_CAPACITY_1,
        reserved_blocks=(
            _reserved_block(id="rb1", class_sections=("c1",), slots=(TimeSlot("mon", "p1"),), resource_id=None),
            _reserved_block(id="rb2", class_sections=("c2",), slots=(TimeSlot("mon", "p1"),), resource_id=None),
        ),
    )
    codes = {e.code for e in run_preflight(problem)}
    assert "RESERVED_RESOURCE_CAPACITY_EXCEEDED" not in codes


def test_capacity_exceeded_multiple_slots_reported_in_deterministic_day_period_order():
    problem = _reserved_problem(
        resources=_ONE_RESOURCE_CAPACITY_1,
        reserved_blocks=(
            _reserved_block(id="rb1", class_sections=("c1",), slots=(TimeSlot("mon", "p2"),), resource_id="gym"),
            _reserved_block(id="rb2", class_sections=("c2",), slots=(TimeSlot("mon", "p2"),), resource_id="gym"),
            _reserved_block(id="rb3", activity_id="club1", class_sections=("c1",), slots=(TimeSlot("mon", "p1"),), resource_id="gym"),
            _reserved_block(id="rb4", activity_id="club1", class_sections=("c2",), slots=(TimeSlot("mon", "p1"),), resource_id="gym"),
        ),
    )
    errors = [e for e in run_preflight(problem) if e.code == "RESERVED_RESOURCE_CAPACITY_EXCEEDED"]
    assert [(e.context["day_id"], e.context["period_id"]) for e in errors] == [("mon", "p1"), ("mon", "p2")]
