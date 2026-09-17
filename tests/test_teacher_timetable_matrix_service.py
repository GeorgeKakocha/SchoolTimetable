"""DB-free contract tests for the whole-school Teacher Matrix projection."""
from __future__ import annotations

from dataclasses import fields
from datetime import datetime, timezone

from school_timetable.application.schedule_models import ActiveScheduleVersion, ScheduleVersionSnapshot
from school_timetable.application.teacher_timetable_matrix_models import (
    TeacherMatrixCell,
    TeacherMatrixDay,
    TeacherMatrixPeriod,
    TeacherMatrixTeacher,
    TeacherTimetableMatrixView,
)
from school_timetable.application.teacher_timetable_matrix_service import (
    TeacherTimetableMatrixService,
)
from school_timetable.application.teacher_timetable_models import (
    TeacherTimetableClassSection,
    TeacherTimetableEntry,
)
from school_timetable.domain.activities import Activity
from school_timetable.domain.calendar import AcademicYear, Day, Period
from school_timetable.domain.groups import ClassSection, ParticipantGroup, ParticipantGroupRole
from school_timetable.domain.people import Teacher
from school_timetable.domain.problem import SchedulingProblem
from school_timetable.domain.result import EntrySource, ScheduleEntry, SolverStatus
from school_timetable.domain.school import School

_CREATED_AT = datetime(2026, 9, 16, tzinfo=timezone.utc)


def _problem(**overrides) -> SchedulingProblem:
    defaults = dict(
        school=School(id="school", name="School"),
        academic_year=AcademicYear(id="year", label="2026/2027"),
        days=(Day("mon", "Monday", 0), Day("tue", "Tuesday", 1)),
        periods=(
            Period("p1", "Period 1", 0, "morning"),
            Period("p2", "Period 2", 1, "morning"),
        ),
        teachers=(
            Teacher("t_zero", "Zero", "Load"),
            Teacher("t_one", "Teacher", "One"),
        ),
        class_sections=(ClassSection("lower", "1-A"), ClassSection("upper", "XII")),
        participant_groups=(
            ParticipantGroup("whole_lower", "Configured whole lower", ("lower",), ParticipantGroupRole.WHOLE_CLASS),
            ParticipantGroup("sub_upper", "Configured upper language", ("upper",), ParticipantGroupRole.SUBGROUP),
            ParticipantGroup("merged", "Configured cross-grade seminar", ("lower", "upper"), ParticipantGroupRole.MERGED_CLASSES),
        ),
        activities=(
            Activity("math", "Mathematics"),
            Activity("language", "Language"),
            Activity("seminar", "Seminar"),
            Activity("club", "Club"),
        ),
        teaching_requirements=(),
    )
    defaults.update(overrides)
    return SchedulingProblem(**defaults)


def _entry(**overrides) -> ScheduleEntry:
    defaults = dict(
        source=EntrySource.REQUIREMENT,
        activity_id="math",
        day_id="mon",
        period_id="p1",
        class_sections=("lower",),
        teacher_id="t_one",
        participant_group_id="whole_lower",
        resource_id=None,
        requirement_id="req_math",
        reserved_block_id=None,
    )
    defaults.update(overrides)
    return ScheduleEntry(**defaults)


def _active(entries=(), **overrides) -> ActiveScheduleVersion:
    values = dict(
        version_number=4,
        solver_status=SolverStatus.FEASIBLE,
        total_soft_penalty=3,
        wall_time_seconds=0.25,
        random_seed=None,
        created_at=_CREATED_AT,
        entries=tuple(entries),
        locked_occurrences=frozenset(),
        configuration_revision_number=7,
    )
    values.update(overrides)
    return ActiveScheduleVersion(**values)


def _snapshot(entries=(), **overrides) -> ScheduleVersionSnapshot:
    values = dict(
        version_number=2,
        solver_status=SolverStatus.OPTIMAL,
        total_soft_penalty=0,
        wall_time_seconds=0.2,
        random_seed=None,
        created_at=_CREATED_AT,
        is_active=False,
        parent_version_number=1,
        configuration_revision_number=3,
        entries=tuple(entries),
        locked_occurrences=frozenset(),
    )
    values.update(overrides)
    return ScheduleVersionSnapshot(**values)


class _ProblemRepository:
    def __init__(self, revisions: dict[int, SchedulingProblem], current: SchedulingProblem | None = None):
        self.revisions = revisions
        self.current = current
        self.revision_calls: list[int] = []
        self.current_calls = 0

    def load_for_revision(self, school_id: str, year_id: str, revision_number: int):
        self.revision_calls.append(revision_number)
        return self.revisions[revision_number]

    def load_by_school_and_year(self, school_id: str, year_id: str):
        self.current_calls += 1
        return self.current


class _ScheduleRepository:
    def __init__(
        self,
        active: ActiveScheduleVersion | None,
        versions: dict[int, ScheduleVersionSnapshot] | None = None,
    ):
        self.active = active
        self.versions = versions or {}

    def get_active_schedule(self, school_id: str, year_id: str):
        return self.active

    def get_version(self, school_id: str, year_id: str, version_number: int):
        if self.active is None and not self.versions:
            return None
        return self.versions[version_number]


def _service(problem: SchedulingProblem, entries=()) -> TeacherTimetableMatrixService:
    return TeacherTimetableMatrixService(
        _ProblemRepository({7: problem}), _ScheduleRepository(_active(entries)),
    )


def _cell(view: TeacherTimetableMatrixView, teacher_id: str, day_id: str, period_id: str):
    teacher = next(teacher for teacher in view.teachers if teacher.id == teacher_id)
    return next(
        (cell for cell in teacher.cells if (cell.day_id, cell.period_id) == (day_id, period_id)),
        None,
    )


def test_dimensions_are_dynamic_ordered_and_sparse_and_include_zero_load_teacher():
    problem = _problem(
        days=(Day("sat", "Saturday", 8), Day("mon", "Monday", 2), Day("wed", "Wednesday", 5)),
        periods=(
            Period("p9", "Period 9", 9, "late"),
            Period("break", "Assembly", 4, "break", is_instructional=False),
            Period("p1", "Period 1", 1, "early"),
            Period("p5", "Period 5", 5, "late"),
        ),
    )
    view = _service(problem, (_entry(day_id="wed", period_id="p5"),)).project("school", "year")

    assert view is not None
    assert [teacher.id for teacher in view.teachers] == ["t_zero", "t_one"]
    assert view.teachers[0].cells == ()
    assert [day.id for day in view.days] == ["mon", "wed", "sat"]
    assert [period.id for period in view.periods] == ["p1", "p5", "p9"]
    assert _cell(view, "t_one", "wed", "p5") is not None
    assert _cell(view, "t_one", "mon", "p1") is None
    assert _cell(view, "t_one", "wed", "break") is None


def test_whole_subgroup_and_merged_entries_preserve_authoritative_semantics_and_labels():
    entries = (
        _entry(),
        _entry(
            activity_id="language", day_id="mon", period_id="p2",
            class_sections=("upper",), participant_group_id="sub_upper",
            requirement_id="req_language",
        ),
        _entry(
            activity_id="seminar", day_id="tue", period_id="p1",
            class_sections=("lower", "upper"), participant_group_id="merged",
            requirement_id="req_seminar", resource_id="room_public_id",
        ),
    )
    view = _service(_problem(), entries).project("school", "year")

    assert view is not None
    whole, subgroup, merged = [cell.entries[0] for cell in view.teachers[1].cells]
    assert (whole.activity_name, whole.participant_group_role) == ("Mathematics", "WHOLE_CLASS")
    assert [(section.id, section.name) for section in whole.class_sections] == [("lower", "1-A")]
    assert subgroup.participant_group_name == "Configured upper language"
    assert subgroup.participant_group_role == "SUBGROUP"
    assert [section.name for section in subgroup.class_sections] == ["XII"]
    assert merged.participant_group_name == "Configured cross-grade seminar"
    assert merged.participant_group_name != "1-A+XII"
    assert merged.participant_group_role == "MERGED_CLASSES"
    assert [(section.id, section.name) for section in merged.class_sections] == [
        ("lower", "1-A"), ("upper", "XII"),
    ]
    assert merged.resource_id == "room_public_id"


def test_teacher_reserved_block_appears_but_teacherless_reserved_block_does_not():
    assigned = _entry(
        source=EntrySource.RESERVED_BLOCK, activity_id="club", day_id="tue", period_id="p2",
        participant_group_id=None, requirement_id=None, reserved_block_id="club_assigned",
    )
    unassigned = _entry(
        source=EntrySource.RESERVED_BLOCK, activity_id="club", day_id="mon", period_id="p2",
        teacher_id=None, participant_group_id=None, requirement_id=None,
        reserved_block_id="club_unassigned",
    )
    view = _service(_problem(), (unassigned, assigned)).project("school", "year")

    assert view is not None
    assert len(view.teachers[1].cells) == 1
    projected = view.teachers[1].cells[0].entries[0]
    assert projected.source is EntrySource.RESERVED_BLOCK
    assert projected.activity_name == "Club"
    assert projected.participant_group_id is None
    assert projected.reserved_block_id == "club_assigned"


def test_multiple_entries_in_one_coordinate_are_preserved_defensively_in_source_order():
    first = _entry(requirement_id="first")
    second = _entry(activity_id="language", participant_group_id="sub_upper", class_sections=("upper",), requirement_id="second")
    view = _service(_problem(), (first, second)).project("school", "year")

    assert view is not None
    cell = _cell(view, "t_one", "mon", "p1")
    assert cell is not None
    assert [entry.requirement_id for entry in cell.entries] == ["first", "second"]


def test_active_projection_loads_only_active_versions_exact_configuration_revision():
    revision_problem = _problem(activities=(Activity("math", "Revision Seven Mathematics"),))
    unrelated_current = _problem(activities=(Activity("math", "Unrelated Draft Mathematics"),))
    problems = _ProblemRepository({7: revision_problem}, current=unrelated_current)
    service = TeacherTimetableMatrixService(problems, _ScheduleRepository(_active((_entry(),))))

    view = service.project("school", "year")

    assert view is not None
    assert view.version_number == 4
    assert view.is_active is True
    assert view.teachers[1].cells[0].entries[0].activity_name == "Revision Seven Mathematics"
    assert problems.revision_calls == [7]
    assert problems.current_calls == 0


def test_historical_projection_loads_only_requested_versions_exact_configuration_revision():
    historical_problem = _problem(activities=(Activity("math", "Historical Mathematics"),))
    unrelated_current = _problem(activities=(Activity("math", "Draft Mathematics"),))
    problems = _ProblemRepository({3: historical_problem}, current=unrelated_current)
    snapshot = _snapshot((_entry(),), version_number=2, configuration_revision_number=3)
    service = TeacherTimetableMatrixService(
        problems, _ScheduleRepository(_active((), version_number=9), {2: snapshot}),
    )

    view = service.project_version("school", "year", 2)

    assert view is not None
    assert view.version_number == 2
    assert view.is_active is False
    assert view.teachers[1].cells[0].entries[0].activity_name == "Historical Mathematics"
    assert problems.revision_calls == [3]
    assert problems.current_calls == 0


def test_no_schedule_returns_none_without_loading_an_unversioned_configuration():
    problems = _ProblemRepository({}, current=_problem())
    service = TeacherTimetableMatrixService(problems, _ScheduleRepository(None))

    assert service.project("school", "year") is None
    assert service.project_version("school", "year", 1) is None
    assert problems.revision_calls == []
    assert problems.current_calls == 0


def test_application_contract_contains_only_public_domain_fields():
    model_types = (
        TeacherMatrixDay,
        TeacherMatrixPeriod,
        TeacherMatrixCell,
        TeacherMatrixTeacher,
        TeacherTimetableMatrixView,
        TeacherTimetableEntry,
        TeacherTimetableClassSection,
    )
    forbidden = {"surrogate_id", "configuration_revision_id", "schedule_version_id", "ordinal"}

    for model_type in model_types:
        assert forbidden.isdisjoint(field.name for field in fields(model_type))
