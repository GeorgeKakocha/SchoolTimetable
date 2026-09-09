from school_timetable.domain.activities import Activity
from school_timetable.domain.calendar import AcademicYear
from school_timetable.domain.groups import ClassSection, ParticipantGroup, ParticipantGroupRole
from school_timetable.domain.people import Teacher
from school_timetable.domain.problem import SchedulingProblem
from school_timetable.domain.requirements import BlockPolicyMode, LessonBlockPolicy, TeachingRequirement
from school_timetable.domain.resources import Resource, ResourceRequirement
from school_timetable.domain.result import SolverStatus
from school_timetable.domain.school import School
from school_timetable.fixtures.common import build_days, build_periods
from school_timetable.fixtures.impossible_fixture import build_impossible_fixture
from school_timetable.scheduling.solver import solve

FLEXIBLE = LessonBlockPolicy(BlockPolicyMode.FLEXIBLE)


def test_impossible_fixture_is_rejected_by_preflight():
    result = solve(build_impossible_fixture())
    assert result.status == SolverStatus.INVALID_INPUT
    assert result.entries == ()
    codes = {e.code for e in result.validation_errors}
    assert "TEACHER_OVERLOADED" in codes


def _build_resource_overcommitted_problem() -> SchedulingProblem:
    """Passes every preflight check (each class is exactly fully occupied,
    no teacher is overloaded) but is still infeasible: two classes each
    need the single capacity-1 gym for 21 periods/week, i.e. 42 total
    demand against 40 available slots in the whole week.
    """
    days = build_days()
    periods = build_periods()
    gym = ResourceRequirement(resource_id="gym")

    return SchedulingProblem(
        school=School(id="s", name="S"),
        academic_year=AcademicYear(id="ay", label="AY"),
        days=days,
        periods=periods,
        teachers=(
            Teacher(id="t_gym_1", first_name="Gym Teacher 1", last_name=""),
            Teacher(id="t_filler_1", first_name="Filler Teacher 1", last_name=""),
            Teacher(id="t_gym_2", first_name="Gym Teacher 2", last_name=""),
            Teacher(id="t_filler_2", first_name="Filler Teacher 2", last_name=""),
        ),
        class_sections=(ClassSection(id="c1", name="C1"), ClassSection(id="c2", name="C2")),
        participant_groups=(
            ParticipantGroup(id="pg_c1", name="PG C1", class_sections=("c1",), role=ParticipantGroupRole.WHOLE_CLASS),
            ParticipantGroup(id="pg_c2", name="PG C2", class_sections=("c2",), role=ParticipantGroupRole.WHOLE_CLASS),
        ),
        activities=(Activity(id="gym_activity", name="Gym Activity"), Activity(id="filler", name="Filler")),
        resources=(Resource(id="gym", name="Indoor Gym", capacity=1),),
        teaching_requirements=(
            TeachingRequirement(
                id="gym_c1", teacher_id="t_gym_1", activity_id="gym_activity", participant_group_id="pg_c1",
                weekly_periods=21, block_policy=FLEXIBLE, resource_requirement=gym,
            ),
            TeachingRequirement(
                id="filler_c1", teacher_id="t_filler_1", activity_id="filler", participant_group_id="pg_c1",
                weekly_periods=19, block_policy=FLEXIBLE,
            ),
            TeachingRequirement(
                id="gym_c2", teacher_id="t_gym_2", activity_id="gym_activity", participant_group_id="pg_c2",
                weekly_periods=21, block_policy=FLEXIBLE, resource_requirement=gym,
            ),
            TeachingRequirement(
                id="filler_c2", teacher_id="t_filler_2", activity_id="filler", participant_group_id="pg_c2",
                weekly_periods=19, block_policy=FLEXIBLE,
            ),
        ),
    )


def test_resource_overcommitment_passes_preflight_but_is_infeasible():
    problem = _build_resource_overcommitted_problem()
    result = solve(problem)
    assert result.status == SolverStatus.INFEASIBLE
    assert result.entries == ()
