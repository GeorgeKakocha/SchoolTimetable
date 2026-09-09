"""Unit tests for persistence -> domain mapping (Phase 3A2.2).

Pure mapper tests: no live database, no `Session`. ORM rows are
constructed directly in memory with explicit surrogate IDs; reference
resolution is exercised via hand-built `NaturalIdLookup` instances.
Fixtures are imported here only as known-good *expected* domain values
(test-only code may do this; production mapper code never imports
`fixtures/`).
"""
from __future__ import annotations

import pytest

from school_timetable.domain.activities import ActivityKind
from school_timetable.domain.blocks import FixedPlacement, ReservedBlock
from school_timetable.domain.calendar import AcademicYear, Day, Period, TimeSlot
from school_timetable.domain.groups import ClassSection, ParticipantGroup, ParticipantGroupRole
from school_timetable.domain.people import AvailabilityStatus, Teacher, TeacherAvailability
from school_timetable.domain.requirements import (
    BlockPolicyMode,
    DistributionPolicy,
    LessonBlockPolicy,
    PreferenceWeight,
    TimePreference,
)
from school_timetable.domain.resources import Resource, ResourceRequirement
from school_timetable.domain.school import School
from school_timetable.fixtures.valid_fixture import build_valid_fixture
from school_timetable.persistence import mappers as mp
from school_timetable.persistence import models as orm


def test_simple_entities_map_natural_id_never_surrogate():
    school_row = orm.School(id=101, natural_id="synthetic-school", name="Synthetic Pilot School")
    assert mp.school_to_domain(school_row) == School(id="synthetic-school", name="Synthetic Pilot School")

    year_row = orm.AcademicYear(id=202, school_id=101, natural_id="ay-2026", label="2026/2027")
    assert mp.academic_year_to_domain(year_row) == AcademicYear(id="ay-2026", label="2026/2027")

    day_row = orm.Day(id=1, academic_year_id=202, natural_id="mon", name="Monday", idx=0)
    assert mp.day_to_domain(day_row) == Day(id="mon", name="Monday", index=0)

    period_row = orm.Period(
        id=2, academic_year_id=202, natural_id="p1", name="Period 1", idx=0,
        block_id="morning", is_instructional=True,
    )
    assert mp.period_to_domain(period_row) == Period(
        id="p1", name="Period 1", index=0, block_id="morning", is_instructional=True,
    )

    class_row = orm.ClassSection(id=3, academic_year_id=202, natural_id="8a", name="8-A", ordinal=0)
    assert mp.class_section_to_domain(class_row) == ClassSection(id="8a", name="8-A")

    teacher_row = orm.Teacher(
        id=4, academic_year_id=202, natural_id="t_math", first_name="Teacher", last_name="Math", ordinal=0,
    )
    assert mp.teacher_to_domain(teacher_row) == Teacher(id="t_math", first_name="Teacher", last_name="Math")

    resource_row = orm.Resource(
        id=5, academic_year_id=202, natural_id="gym", name="Indoor Gym", capacity=1, ordinal=0,
    )
    assert mp.resource_to_domain(resource_row) == Resource(id="gym", name="Indoor Gym", capacity=1)

    # Enum text -> domain enum, both Activity.kind values.
    ordinary_row = orm.Activity(id=6, academic_year_id=202, natural_id="math", name="Mathematics",
                                 kind="ORDINARY", ordinal=0)
    club_row = orm.Activity(id=7, academic_year_id=202, natural_id="club_chess", name="Chess Club",
                             kind="CLUB", ordinal=1)
    assert mp.activity_to_domain(ordinary_row).kind is ActivityKind.ORDINARY
    assert mp.activity_to_domain(club_row).kind is ActivityKind.CLUB

    # None of the above ever leaks a surrogate id: every domain `.id` is
    # the string natural_id, never the int surrogate.
    for domain_obj, surrogate_id in [
        (mp.school_to_domain(school_row), school_row.id),
        (mp.day_to_domain(day_row), day_row.id),
        (mp.teacher_to_domain(teacher_row), teacher_row.id),
    ]:
        assert domain_obj.id != surrogate_id
        assert isinstance(domain_obj.id, str)


def test_teacher_availability_maps_status_enum_via_lookup():
    lookup = mp.NaturalIdLookup(teachers={4: "t_science"}, days={1: "fri"}, periods={2: "p7"})
    row = orm.TeacherAvailability(
        academic_year_id=202, teacher_id=4, day_id=1, period_id=2, status="UNAVAILABLE", ordinal=0,
    )
    result = mp.teacher_availability_to_domain(row, lookup)
    assert result == TeacherAvailability(
        teacher_id="t_science", day_id="fri", period_id="p7", status=AvailabilityStatus.UNAVAILABLE,
    )


def test_participant_group_orders_class_sections_by_ordinal_not_input_order():
    lookup = mp.NaturalIdLookup(class_sections={10: "9a", 11: "9b"})
    group_row = orm.ParticipantGroup(
        id=99, academic_year_id=202, natural_id="pg_9a_9b_merged", name="Merged",
        role="MERGED_CLASSES", ordinal=0,
    )
    # Deliberately out of order: ordinal=1 (9b) listed before ordinal=0 (9a).
    memberships = [
        orm.ParticipantGroupClassSection(academic_year_id=202, participant_group_id=99, class_section_id=11, ordinal=1),
        orm.ParticipantGroupClassSection(academic_year_id=202, participant_group_id=99, class_section_id=10, ordinal=0),
    ]
    result = mp.participant_group_to_domain(group_row, memberships, lookup)
    assert result == ParticipantGroup(
        id="pg_9a_9b_merged", name="Merged", class_sections=("9a", "9b"),
        role=ParticipantGroupRole.MERGED_CLASSES,
    )


def test_time_preference_maps_array_to_tuple_and_weight_enum():
    row = orm.TimePreference(
        academic_year_id=202, teaching_requirement_id=55, ordinal=0,
        preferred_period_indexes=[0, 1], weight="HIGH",
    )
    result = mp.time_preference_to_domain(row)
    assert result == TimePreference(preferred_periods=(0, 1), weight=PreferenceWeight.HIGH)
    assert isinstance(result.preferred_periods, tuple)


@pytest.mark.parametrize(
    "block_mode,block_sizes,expected_mode",
    [
        ("REQUIRED", [3, 1], BlockPolicyMode.REQUIRED),
        ("PREFERRED", [2, 1, 1, 1], BlockPolicyMode.PREFERRED),
        ("FLEXIBLE", [], BlockPolicyMode.FLEXIBLE),
    ],
)
def test_teaching_requirement_block_policy_modes(block_mode, block_sizes, expected_mode):
    lookup = mp.NaturalIdLookup(
        teachers={1: "t_math"}, activities={2: "math"}, participant_groups={3: "pg_8a"},
    )
    row = orm.TeachingRequirement(
        id=42, academic_year_id=202, natural_id="math_8a", teacher_id=1, activity_id=2,
        participant_group_id=3, weekly_periods=5, block_mode=block_mode, block_sizes=block_sizes,
        min_distinct_days=None, max_periods_per_day=None, resource_id=None, split_group_id=None,
        ordinal=0,
    )
    result = mp.teaching_requirement_to_domain(row, [], lookup)
    assert result.block_policy == LessonBlockPolicy(mode=expected_mode, block_sizes=tuple(block_sizes))
    assert isinstance(result.block_policy.block_sizes, tuple)


def test_teaching_requirement_matches_real_fixture_object_exactly():
    """Cross-check against the actual `build_valid_fixture()` domain
    object -- the strongest possible proof that the mapper reconstructs
    a genuinely equal `TeachingRequirement`, not just a plausible one."""
    problem = build_valid_fixture()
    expected = next(r for r in problem.teaching_requirements if r.id == "math_8a")
    assert expected.weekly_periods == 5
    assert expected.block_policy == LessonBlockPolicy(BlockPolicyMode.REQUIRED, block_sizes=(2, 1, 1, 1))

    lookup = mp.NaturalIdLookup(
        teachers={1: "t_math"}, activities={2: "math"}, participant_groups={3: "pg_8a"},
    )
    row = orm.TeachingRequirement(
        id=42, academic_year_id=202, natural_id="math_8a", teacher_id=1, activity_id=2,
        participant_group_id=3, weekly_periods=5, block_mode="REQUIRED", block_sizes=[2, 1, 1, 1],
        min_distinct_days=None, max_periods_per_day=None, resource_id=None, split_group_id=None,
        ordinal=0,
    )
    result = mp.teaching_requirement_to_domain(row, [], lookup)
    assert result == expected


def test_teaching_requirement_distribution_policy_and_multiple_time_preferences():
    lookup = mp.NaturalIdLookup(
        teachers={1: "t_history"}, activities={2: "history"}, participant_groups={3: "pg_8a"},
    )
    row = orm.TeachingRequirement(
        id=42, academic_year_id=202, natural_id="history_8a", teacher_id=1, activity_id=2,
        participant_group_id=3, weekly_periods=9, block_mode="FLEXIBLE", block_sizes=[],
        min_distinct_days=5, max_periods_per_day=None, resource_id=None, split_group_id=None,
        ordinal=0,
    )
    # Deliberately out of order (ordinal 1 before ordinal 0).
    time_prefs = [
        orm.TimePreference(academic_year_id=202, teaching_requirement_id=42, ordinal=1,
                            preferred_period_indexes=[6, 7], weight="LOW"),
        orm.TimePreference(academic_year_id=202, teaching_requirement_id=42, ordinal=0,
                            preferred_period_indexes=[0, 1], weight="MEDIUM"),
    ]
    result = mp.teaching_requirement_to_domain(row, time_prefs, lookup)

    assert result.distribution_policy == DistributionPolicy(min_distinct_days=5, max_periods_per_day=None)
    assert result.time_preferences == (
        TimePreference(preferred_periods=(0, 1), weight=PreferenceWeight.MEDIUM),
        TimePreference(preferred_periods=(6, 7), weight=PreferenceWeight.LOW),
    )


def test_teaching_requirement_resource_requirement_present_and_absent():
    lookup = mp.NaturalIdLookup(
        teachers={1: "t_sport"}, activities={2: "sport"}, participant_groups={3: "pg_8a"},
        resources={9: "gym"},
    )
    with_resource = orm.TeachingRequirement(
        id=42, academic_year_id=202, natural_id="sport_8a", teacher_id=1, activity_id=2,
        participant_group_id=3, weekly_periods=2, block_mode="FLEXIBLE", block_sizes=[],
        min_distinct_days=None, max_periods_per_day=None, resource_id=9, split_group_id=None, ordinal=0,
    )
    without_resource = orm.TeachingRequirement(
        id=43, academic_year_id=202, natural_id="art_8a", teacher_id=1, activity_id=2,
        participant_group_id=3, weekly_periods=2, block_mode="FLEXIBLE", block_sizes=[],
        min_distinct_days=None, max_periods_per_day=None, resource_id=None, split_group_id=None, ordinal=1,
    )
    assert mp.teaching_requirement_to_domain(with_resource, [], lookup).resource_requirement == (
        ResourceRequirement(resource_id="gym")
    )
    assert mp.teaching_requirement_to_domain(without_resource, [], lookup).resource_requirement is None


def test_teaching_requirement_split_group_id_preserved_verbatim():
    lookup = mp.NaturalIdLookup(
        teachers={1: "t_german"}, activities={2: "german"}, participant_groups={3: "pg_8a_german"},
    )
    row = orm.TeachingRequirement(
        id=42, academic_year_id=202, natural_id="german_8a", teacher_id=1, activity_id=2,
        participant_group_id=3, weekly_periods=3, block_mode="FLEXIBLE", block_sizes=[],
        min_distinct_days=None, max_periods_per_day=None, resource_id=None,
        split_group_id="split_lang_8a", ordinal=0,
    )
    result = mp.teaching_requirement_to_domain(row, [], lookup)
    assert result.split_group_id == "split_lang_8a"


def test_reserved_block_orders_children_by_ordinal_and_handles_optional_teacher():
    lookup = mp.NaturalIdLookup(
        activities={1: "club_chess"}, teachers={2: "t_art"}, class_sections={3: "8a", 4: "8b"},
        days={5: "wed"}, periods={6: "p8"},
    )
    row = orm.ReservedBlock(
        id=77, academic_year_id=202, natural_id="club_chess", name="Chess Club",
        activity_id=1, teacher_id=2, ordinal=0,
    )
    # Deliberately out of order for both child collections.
    class_rows = [
        orm.ReservedBlockClassSection(academic_year_id=202, reserved_block_id=77, class_section_id=4, ordinal=1),
        orm.ReservedBlockClassSection(academic_year_id=202, reserved_block_id=77, class_section_id=3, ordinal=0),
    ]
    slot_rows = [
        orm.ReservedBlockSlot(academic_year_id=202, reserved_block_id=77, day_id=5, period_id=6, ordinal=0),
    ]
    result = mp.reserved_block_to_domain(row, class_rows, slot_rows, lookup)
    assert result == ReservedBlock(
        id="club_chess", name="Chess Club", activity_id="club_chess",
        class_sections=("8a", "8b"), slots=(TimeSlot("wed", "p8"),), teacher_id="t_art",
    )

    # Optional teacher_id None on the ORM row must stay None in domain.
    no_teacher_row = orm.ReservedBlock(
        id=78, academic_year_id=202, natural_id="club_robotics", name="Robotics Club",
        activity_id=1, teacher_id=None, ordinal=1,
    )
    result_no_teacher = mp.reserved_block_to_domain(no_teacher_row, [], [], lookup)
    assert result_no_teacher.teacher_id is None


def test_fixed_placement_reconstructs_time_slot():
    lookup = mp.NaturalIdLookup(
        teaching_requirements={1: "art_8b"}, days={2: "mon"}, periods={3: "p1"},
    )
    row = orm.FixedPlacement(
        id=88, academic_year_id=202, natural_id="fixed_art_8b",
        teaching_requirement_id=1, day_id=2, period_id=3, ordinal=0,
    )
    result = mp.fixed_placement_to_domain(row, lookup)
    assert result == FixedPlacement(id="fixed_art_8b", requirement_id="art_8b", slot=TimeSlot("mon", "p1"))


def test_unresolved_surrogate_reference_fails_clearly():
    empty_lookup = mp.NaturalIdLookup()
    row = orm.TeacherAvailability(
        academic_year_id=202, teacher_id=999, day_id=1, period_id=2, status="AVAILABLE", ordinal=0,
    )
    with pytest.raises(KeyError, match="Teacher"):
        mp.teacher_availability_to_domain(row, empty_lookup)
