"""Pure, DB-free tests for Phase 3B.1's `ClassTimetableService`
(`docs/DECISIONS.md` #32). No database, no FastAPI, no CP-SAT solve --
both repository ports are small in-memory fakes returning real domain/
application objects, so every projection rule (membership, grouping,
ordering, name resolution) can be exercised in isolation.

The real-solver/real-verifier/real-PostgreSQL end-to-end proof (split
groups, merged groups, reserved blocks, calendar ordering, HTTP error
mapping) lives in `tests_web/test_class_timetable_api.py`.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from school_timetable.application.class_timetable_service import ClassTimetableService
from school_timetable.application.errors import ClassSectionNotFoundError, SchedulingProblemNotFoundError
from school_timetable.application.schedule_models import ActiveScheduleVersion
from school_timetable.domain.activities import Activity
from school_timetable.domain.calendar import AcademicYear, Day, Period
from school_timetable.domain.groups import ClassSection, ParticipantGroup
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
        teachers=(Teacher(id="t1", name="Teacher One"),),
        class_sections=(ClassSection(id="8a", name="8-A"), ClassSection(id="9a", name="9-A")),
        participant_groups=(ParticipantGroup(id="g1", name="All of 8-A", class_sections=("8a",)),),
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
        raise NotImplementedError("not used by ClassTimetableService")


def test_unknown_school_year_propagates_not_found_unchanged():
    error = SchedulingProblemNotFoundError("school-1", "year-1")
    service = ClassTimetableService(_FakeProblemRepository(error=error), _FakeScheduleRepository(active=None))

    with pytest.raises(SchedulingProblemNotFoundError) as exc_info:
        service.project("school-1", "year-1", "8a")

    assert exc_info.value is error


def test_unknown_class_raises_class_section_not_found():
    problem = _problem()
    service = ClassTimetableService(_FakeProblemRepository(problem), _FakeScheduleRepository(active=None))

    with pytest.raises(ClassSectionNotFoundError) as exc_info:
        service.project("school-1", "year-1", "no-such-class")

    assert exc_info.value.school_natural_id == "school-1"
    assert exc_info.value.academic_year_natural_id == "year-1"
    assert exc_info.value.class_section_id == "no-such-class"


def test_no_active_schedule_returns_none():
    problem = _problem()
    service = ClassTimetableService(_FakeProblemRepository(problem), _FakeScheduleRepository(active=None))

    assert service.project("school-1", "year-1", "8a") is None


def test_ordinary_lesson_projects_one_correct_entry():
    problem = _problem()
    active = _active((_entry(),))
    service = ClassTimetableService(_FakeProblemRepository(problem), _FakeScheduleRepository(active))

    view = service.project("school-1", "year-1", "8a")

    assert view is not None
    assert view.school_id == "school-1"
    assert view.class_section_id == "8a"
    assert view.class_section_name == "8-A"
    assert view.version_number == 1
    assert view.is_active is True

    cell = next(c for row in view.rows for c in row.cells if c.day_id == "mon" and row.period_id == "p1")
    assert len(cell.entries) == 1
    entry = cell.entries[0]
    assert entry.activity_id == "math"
    assert entry.activity_name == "Mathematics"
    assert entry.teacher_id == "t1"
    assert entry.teacher_name == "Teacher One"
    assert entry.participant_group_id == "g1"
    assert entry.participant_group_name == "All of 8-A"
    assert entry.requirement_id == "req1"
    assert entry.reserved_block_id is None


def test_entry_for_another_class_does_not_appear():
    problem = _problem()
    active = _active((_entry(class_sections=("9a",)),))
    service = ClassTimetableService(_FakeProblemRepository(problem), _FakeScheduleRepository(active))

    view = service.project("school-1", "year-1", "8a")

    assert view is not None
    all_entries = [e for row in view.rows for cell in row.cells for e in cell.entries]
    assert all_entries == []


def test_split_parallel_entries_both_survive_in_same_cell():
    problem = _problem(
        participant_groups=(
            ParticipantGroup(id="g_german", name="8-A German", class_sections=("8a",)),
            ParticipantGroup(id="g_russian", name="8-A Russian", class_sections=("8a",)),
        ),
        activities=(Activity(id="german", name="German"), Activity(id="russian", name="Russian")),
        teachers=(Teacher(id="t_german", name="Teacher German"), Teacher(id="t_russian", name="Teacher Russian")),
    )
    german = _entry(
        activity_id="german", teacher_id="t_german", participant_group_id="g_german", requirement_id="german_8a",
    )
    russian = _entry(
        activity_id="russian", teacher_id="t_russian", participant_group_id="g_russian", requirement_id="russian_8a",
    )
    active = _active((german, russian))
    service = ClassTimetableService(_FakeProblemRepository(problem), _FakeScheduleRepository(active))

    view = service.project("school-1", "year-1", "8a")

    assert view is not None
    cell = next(c for row in view.rows for c in row.cells if c.day_id == "mon" and row.period_id == "p1")
    assert len(cell.entries) == 2
    assert {e.requirement_id for e in cell.entries} == {"german_8a", "russian_8a"}


def test_within_cell_order_matches_persisted_entry_order():
    problem = _problem()
    first = _entry(requirement_id="req-first")
    second = _entry(requirement_id="req-second")
    active = _active((first, second))
    service = ClassTimetableService(_FakeProblemRepository(problem), _FakeScheduleRepository(active))

    view = service.project("school-1", "year-1", "8a")

    assert view is not None
    cell = next(c for row in view.rows for c in row.cells if c.day_id == "mon" and row.period_id == "p1")
    assert [e.requirement_id for e in cell.entries] == ["req-first", "req-second"]

    # Reversed persisted order -> reversed projected order (proves no
    # incidental re-sort happens anywhere in the pipeline).
    active_reversed = _active((second, first))
    service_reversed = ClassTimetableService(_FakeProblemRepository(problem), _FakeScheduleRepository(active_reversed))
    view_reversed = service_reversed.project("school-1", "year-1", "8a")
    assert view_reversed is not None
    cell_reversed = next(
        c for row in view_reversed.rows for c in row.cells if c.day_id == "mon" and row.period_id == "p1"
    )
    assert [e.requirement_id for e in cell_reversed.entries] == ["req-second", "req-first"]


def test_merged_entry_appears_once_in_each_affected_class_projection():
    problem = _problem(
        participant_groups=(
            ParticipantGroup(id="g_merged", name="9-A + 9-B Merged", class_sections=("9a", "9b")),
        ),
        class_sections=(ClassSection(id="9a", name="9-A"), ClassSection(id="9b", name="9-B")),
        activities=(Activity(id="history", name="History"),),
    )
    merged_entry = _entry(
        activity_id="history", class_sections=("9a", "9b"), participant_group_id="g_merged",
        requirement_id="history_merged",
    )
    active = _active((merged_entry,))

    service_9a = ClassTimetableService(_FakeProblemRepository(problem), _FakeScheduleRepository(active))
    view_9a = service_9a.project("school-1", "year-1", "9a")
    service_9b = ClassTimetableService(_FakeProblemRepository(problem), _FakeScheduleRepository(active))
    view_9b = service_9b.project("school-1", "year-1", "9b")

    assert view_9a is not None and view_9b is not None
    cell_9a = next(c for row in view_9a.rows for c in row.cells if c.day_id == "mon" and row.period_id == "p1")
    cell_9b = next(c for row in view_9b.rows for c in row.cells if c.day_id == "mon" and row.period_id == "p1")
    assert [e.requirement_id for e in cell_9a.entries] == ["history_merged"]
    assert [e.requirement_id for e in cell_9b.entries] == ["history_merged"]


def test_reserved_block_entry_no_fake_group_and_teacher_none_preserved():
    problem = _problem(activities=(Activity(id="club_chess", name="Chess Club"),))
    entry = _entry(
        source=EntrySource.RESERVED_BLOCK, activity_id="club_chess", teacher_id=None,
        participant_group_id=None, requirement_id=None, reserved_block_id="club_chess",
    )
    active = _active((entry,))
    service = ClassTimetableService(_FakeProblemRepository(problem), _FakeScheduleRepository(active))

    view = service.project("school-1", "year-1", "8a")

    assert view is not None
    cell = next(c for row in view.rows for c in row.cells if c.day_id == "mon" and row.period_id == "p1")
    assert len(cell.entries) == 1
    projected = cell.entries[0]
    assert projected.source == EntrySource.RESERVED_BLOCK
    assert projected.activity_name == "Chess Club"
    assert projected.teacher_id is None
    assert projected.teacher_name is None
    assert projected.participant_group_id is None
    assert projected.participant_group_name is None
    assert projected.reserved_block_id == "club_chess"


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
    service = ClassTimetableService(_FakeProblemRepository(problem), _FakeScheduleRepository(active))

    view = service.project("school-1", "year-1", "8a")

    assert view is not None
    assert [d.id for d in view.days] == ["mon", "tue"]
    assert [row.period_id for row in view.rows] == ["p1", "p2"]
    assert "lunch" not in [row.period_id for row in view.rows]


def test_strict_lookup_raises_on_missing_referenced_activity():
    problem = _problem(activities=())  # "math" activity missing from config
    active = _active((_entry(),))
    service = ClassTimetableService(_FakeProblemRepository(problem), _FakeScheduleRepository(active))

    with pytest.raises(KeyError):
        service.project("school-1", "year-1", "8a")
