"""Pure, DB-free tests for `TeacherTimetableService` (next product
slice after Phase 3C.3, no new phase number). No database, no FastAPI,
no CP-SAT solve -- both repository ports are small in-memory fakes
returning real domain/application objects, so every projection rule
(membership, grouping, ordering, name/role resolution) can be
exercised in isolation. Mirrors `tests/test_class_timetable_service.py`
exactly, adapted for teacher-instead-of-class membership.

The real-solver/real-verifier/real-PostgreSQL end-to-end proof
(SUBGROUP/MERGED_CLASSES targets, calendar ordering, HTTP error
mapping) lives in `tests_web/test_teacher_timetable_api.py`.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from school_timetable.application.errors import SchedulingProblemNotFoundError, TeacherNotFoundError
from school_timetable.application.schedule_models import ActiveScheduleVersion
from school_timetable.application.teacher_timetable_service import TeacherTimetableService
from school_timetable.domain.activities import Activity
from school_timetable.domain.calendar import AcademicYear, Day, Period
from school_timetable.domain.groups import ClassSection, ParticipantGroup, ParticipantGroupRole
from school_timetable.domain.people import Teacher
from school_timetable.domain.problem import SchedulingProblem
from school_timetable.domain.result import EntrySource, ScheduleEntry, SolverStatus
from school_timetable.domain.school import School

_CREATED_AT = datetime(2024, 1, 1, tzinfo=timezone.utc)


def _problem(**overrides) -> SchedulingProblem:
    defaults = dict(
        school=School(id="school-1", name="Pilot School"),
        academic_year=AcademicYear(id="year-1", label="2025/2026"),
        days=(Day(id="mon", name="Monday", index=0), Day(id="tue", name="Tuesday", index=1)),
        periods=(
            Period(id="p1", name="Period 1", index=0, block_id="morning"),
            Period(id="p2", name="Period 2", index=1, block_id="morning"),
        ),
        teachers=(Teacher(id="t1", first_name="Teacher One", last_name=""),),
        class_sections=(ClassSection(id="8a", name="8-A"), ClassSection(id="9a", name="9-A")),
        participant_groups=(ParticipantGroup(id="g1", name="All of 8-A", class_sections=("8a",), role=ParticipantGroupRole.WHOLE_CLASS),),
        activities=(Activity(id="math", name="Mathematics"),),
        teaching_requirements=(),
    )
    defaults.update(overrides)
    return SchedulingProblem(**defaults)


def _active(entries: tuple[ScheduleEntry, ...], **overrides) -> ActiveScheduleVersion:
    defaults = dict(
        version_number=1, solver_status=SolverStatus.OPTIMAL, total_soft_penalty=0,
        wall_time_seconds=1.0, random_seed=None, created_at=_CREATED_AT,
        entries=entries, locked_occurrences=frozenset(),
    )
    defaults.update(overrides)
    return ActiveScheduleVersion(**defaults)


def _entry(**overrides) -> ScheduleEntry:
    defaults = dict(
        source=EntrySource.REQUIREMENT, activity_id="math", day_id="mon", period_id="p1",
        class_sections=("8a",), teacher_id="t1", participant_group_id="g1",
        resource_id=None, requirement_id="req1", reserved_block_id=None,
    )
    defaults.update(overrides)
    return ScheduleEntry(**defaults)


class _FakeProblemRepository:
    def __init__(self, problem: SchedulingProblem | None = None, error: Exception | None = None):
        self._problem = problem
        self._error = error

    def load_by_school_and_year(self, school_natural_id: str, academic_year_natural_id: str):
        if self._error is not None:
            raise self._error
        return self._problem


class _FakeScheduleRepository:
    def __init__(self, active: ActiveScheduleVersion | None):
        self._active = active

    def get_active_schedule(self, school_natural_id: str, academic_year_natural_id: str):
        return self._active

    def persist_initial_version(self, *args, **kwargs):
        raise NotImplementedError("not used by TeacherTimetableService")


def test_unknown_school_year_propagates_not_found_unchanged():
    error = SchedulingProblemNotFoundError("school-1", "year-1")
    service = TeacherTimetableService(_FakeProblemRepository(error=error), _FakeScheduleRepository(active=None))

    with pytest.raises(SchedulingProblemNotFoundError) as exc_info:
        service.project("school-1", "year-1", "t1")

    assert exc_info.value is error


def test_unknown_teacher_raises_teacher_not_found():
    problem = _problem()
    service = TeacherTimetableService(_FakeProblemRepository(problem), _FakeScheduleRepository(active=None))

    with pytest.raises(TeacherNotFoundError) as exc_info:
        service.project("school-1", "year-1", "no-such-teacher")

    assert exc_info.value.school_natural_id == "school-1"
    assert exc_info.value.academic_year_natural_id == "year-1"
    assert exc_info.value.teacher_id == "no-such-teacher"


def test_no_active_schedule_returns_none():
    problem = _problem()
    service = TeacherTimetableService(_FakeProblemRepository(problem), _FakeScheduleRepository(active=None))

    assert service.project("school-1", "year-1", "t1") is None


def test_ordinary_whole_class_lesson_projects_one_correct_entry():
    problem = _problem()
    active = _active((_entry(),))
    service = TeacherTimetableService(_FakeProblemRepository(problem), _FakeScheduleRepository(active))

    view = service.project("school-1", "year-1", "t1")

    assert view is not None
    assert view.school_id == "school-1"
    assert view.teacher_id == "t1"
    assert view.teacher_name == "Teacher One"
    assert view.version_number == 1
    assert view.is_active is True

    cell = next(c for row in view.rows for c in row.cells if c.day_id == "mon" and row.period_id == "p1")
    assert len(cell.entries) == 1
    entry = cell.entries[0]
    assert entry.activity_id == "math"
    assert entry.activity_name == "Mathematics"
    assert entry.participant_group_id == "g1"
    assert entry.participant_group_name == "All of 8-A"
    assert entry.participant_group_role == "WHOLE_CLASS"
    assert [c.id for c in entry.class_sections] == ["8a"]
    assert [c.name for c in entry.class_sections] == ["8-A"]
    assert entry.requirement_id == "req1"
    assert entry.reserved_block_id is None


def test_entry_for_another_teacher_does_not_appear():
    problem = _problem(teachers=(Teacher(id="t1", first_name="Teacher One", last_name=""), Teacher(id="t2", first_name="Teacher Two", last_name="")))
    active = _active((_entry(teacher_id="t2"),))
    service = TeacherTimetableService(_FakeProblemRepository(problem), _FakeScheduleRepository(active))

    view = service.project("school-1", "year-1", "t1")

    assert view is not None
    all_entries = [e for row in view.rows for cell in row.cells for e in cell.entries]
    assert all_entries == []


def test_only_requested_teacher_entries_appear_when_multiple_teachers_have_lessons():
    problem = _problem(
        teachers=(Teacher(id="t1", first_name="Teacher One", last_name=""), Teacher(id="t2", first_name="Teacher Two", last_name="")),
        activities=(Activity(id="math", name="Mathematics"), Activity(id="art", name="Art")),
    )
    mine = _entry(teacher_id="t1", requirement_id="req-mine")
    other = _entry(
        teacher_id="t2", activity_id="art", period_id="p2", requirement_id="req-other",
    )
    active = _active((mine, other))
    service = TeacherTimetableService(_FakeProblemRepository(problem), _FakeScheduleRepository(active))

    view = service.project("school-1", "year-1", "t1")

    assert view is not None
    all_entries = [e for row in view.rows for cell in row.cells for e in cell.entries]
    assert [e.requirement_id for e in all_entries] == ["req-mine"]


def test_subgroup_target_reports_authoritative_role_and_class_sections():
    problem = _problem(
        participant_groups=(
            ParticipantGroup(id="g_german", name="8-A German", class_sections=("8a",), role=ParticipantGroupRole.SUBGROUP),
        ),
        activities=(Activity(id="german", name="German"),),
        teachers=(Teacher(id="t_german", first_name="Teacher German", last_name=""),),
    )
    entry = _entry(
        activity_id="german", teacher_id="t_german", participant_group_id="g_german", requirement_id="german_8a",
    )
    active = _active((entry,))
    service = TeacherTimetableService(_FakeProblemRepository(problem), _FakeScheduleRepository(active))

    view = service.project("school-1", "year-1", "t_german")

    assert view is not None
    cell = next(c for row in view.rows for c in row.cells if c.day_id == "mon" and row.period_id == "p1")
    assert len(cell.entries) == 1
    projected = cell.entries[0]
    assert projected.participant_group_name == "8-A German"
    assert projected.participant_group_role == "SUBGROUP"
    assert [c.id for c in projected.class_sections] == ["8a"]


def test_merged_classes_target_reports_authoritative_role_and_both_class_sections():
    problem = _problem(
        participant_groups=(
            ParticipantGroup(id="g_merged", name="9-A + 9-B Merged", class_sections=("9a", "9b"), role=ParticipantGroupRole.MERGED_CLASSES),
        ),
        class_sections=(ClassSection(id="9a", name="9-A"), ClassSection(id="9b", name="9-B")),
        activities=(Activity(id="history", name="History"),),
        teachers=(Teacher(id="t_history", first_name="Teacher History", last_name=""),),
    )
    entry = _entry(
        activity_id="history", teacher_id="t_history", class_sections=("9a", "9b"),
        participant_group_id="g_merged", requirement_id="history_merged",
    )
    active = _active((entry,))
    service = TeacherTimetableService(_FakeProblemRepository(problem), _FakeScheduleRepository(active))

    view = service.project("school-1", "year-1", "t_history")

    assert view is not None
    cell = next(c for row in view.rows for c in row.cells if c.day_id == "mon" and row.period_id == "p1")
    assert len(cell.entries) == 1
    projected = cell.entries[0]
    assert projected.participant_group_name == "9-A + 9-B Merged"
    assert projected.participant_group_role == "MERGED_CLASSES"
    assert [c.id for c in projected.class_sections] == ["9a", "9b"]
    assert [c.name for c in projected.class_sections] == ["9-A", "9-B"]


def test_reserved_block_entry_no_fake_group_and_no_group_role():
    problem = _problem(activities=(Activity(id="club_chess", name="Chess Club"),))
    entry = _entry(
        source=EntrySource.RESERVED_BLOCK, activity_id="club_chess", class_sections=("8a",),
        participant_group_id=None, requirement_id=None, reserved_block_id="club_chess",
    )
    active = _active((entry,))
    service = TeacherTimetableService(_FakeProblemRepository(problem), _FakeScheduleRepository(active))

    view = service.project("school-1", "year-1", "t1")

    assert view is not None
    cell = next(c for row in view.rows for c in row.cells if c.day_id == "mon" and row.period_id == "p1")
    assert len(cell.entries) == 1
    projected = cell.entries[0]
    assert projected.source == EntrySource.RESERVED_BLOCK
    assert projected.activity_name == "Chess Club"
    assert projected.participant_group_id is None
    assert projected.participant_group_name is None
    assert projected.participant_group_role is None
    assert projected.reserved_block_id == "club_chess"


def test_free_periods_are_empty_entry_tuples():
    problem = _problem()
    active = _active((_entry(),))
    service = TeacherTimetableService(_FakeProblemRepository(problem), _FakeScheduleRepository(active))

    view = service.project("school-1", "year-1", "t1")

    assert view is not None
    free_cell = next(c for row in view.rows for c in row.cells if c.day_id == "tue" and row.period_id == "p1")
    assert free_cell.entries == ()


def test_teacher_with_zero_entries_returns_valid_all_empty_grid():
    problem = _problem()
    active = _active(())
    service = TeacherTimetableService(_FakeProblemRepository(problem), _FakeScheduleRepository(active))

    view = service.project("school-1", "year-1", "t1")

    assert view is not None
    all_entries = [e for row in view.rows for cell in row.cells for e in cell.entries]
    assert all_entries == []
    assert len(view.rows) == 2
    assert all(len(row.cells) == 2 for row in view.rows)


def test_calendar_ordering_and_non_instructional_filter():
    problem = _problem(
        days=(Day(id="tue", name="Tuesday", index=1), Day(id="mon", name="Monday", index=0)),
        periods=(
            Period(id="p2", name="Period 2", index=1, block_id="morning"),
            Period(id="lunch", name="Lunch", index=99, block_id="break", is_instructional=False),
            Period(id="p1", name="Period 1", index=0, block_id="morning"),
        ),
    )
    active = _active(())
    service = TeacherTimetableService(_FakeProblemRepository(problem), _FakeScheduleRepository(active))

    view = service.project("school-1", "year-1", "t1")

    assert view is not None
    assert [d.id for d in view.days] == ["mon", "tue"]
    assert [row.period_id for row in view.rows] == ["p1", "p2"]
    assert "lunch" not in [row.period_id for row in view.rows]


def test_strict_lookup_raises_on_missing_referenced_activity():
    problem = _problem(activities=())  # "math" activity missing from config
    active = _active((_entry(),))
    service = TeacherTimetableService(_FakeProblemRepository(problem), _FakeScheduleRepository(active))

    with pytest.raises(KeyError):
        service.project("school-1", "year-1", "t1")


def test_strict_lookup_raises_on_missing_referenced_class_section():
    problem = _problem(class_sections=())  # "8a" class section missing from config
    active = _active((_entry(),))
    service = TeacherTimetableService(_FakeProblemRepository(problem), _FakeScheduleRepository(active))

    with pytest.raises(KeyError):
        service.project("school-1", "year-1", "t1")
