"""Pure orchestration/validation tests for Calendar A's `CalendarService`
and `calendar_rules`. No database, no PostgreSQL -- all repository ports
are small in-memory fakes; real-PostgreSQL persistence/concurrency proof
lives in `tests_web/test_calendar_repository.py`.

`Day`/`Period` are the existing `domain.calendar.Day`/`Period` -- this
slice only adds the missing catalog write use case over them, plus two
new optional `Period.start_time`/`end_time` fields.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import time

import pytest

from school_timetable.application.calendar_models import DayFields, PeriodFields
from school_timetable.application.calendar_service import CalendarService
from school_timetable.application.errors import (
    ConfigurationLockedError,
    DayInUseError,
    DayNotFoundError,
    DuplicateDayError,
    DuplicatePeriodError,
    InvalidDayError,
    InvalidPeriodError,
    PeriodInUseError,
    PeriodNotFoundError,
    PeriodReorderBlockedError,
)
from school_timetable.domain.calendar import Day, Period, TimeSlot
from school_timetable.domain.blocks import ReservedBlock
from school_timetable.domain.people import AvailabilityStatus, TeacherAvailability
from school_timetable.domain.requirements import TimePreference, PreferenceWeight
from school_timetable.fixtures.valid_fixture import build_valid_fixture

_SCHOOL = "synthetic-school"
_YEAR = "ay-2026"


class _FakeProblemRepository:
    def __init__(self, problem):
        self._problem = problem

    def load_by_school_and_year(self, school_natural_id, academic_year_natural_id):
        return self._problem


class _FakeDayWritePort:
    """Simulates the authoritative, lock-protected recheck by simply
    re-invoking `validate` against the same (unchanged) problem -- the
    genuine lock/reload-under-lock mechanics are proven for real against
    PostgreSQL in `tests_web/test_calendar_repository.py`.

    `locked=True` simulates the real repository's own authoritative
    lock-rejection outcome -- raised BEFORE `validate` is ever invoked
    (Safe Configuration Changes, Slice B: `CalendarService` itself no
    longer has any fast, un-locked precheck of its own)."""

    def __init__(self, problem, locked: bool = False):
        self._problem = problem
        self._locked = locked
        self.create_calls: list[tuple] = []
        self.update_calls: list[tuple] = []
        self.delete_calls: list[str] = []
        self.move_calls: list[tuple] = []

    def create(self, school, year, day_natural_id, name, validate):
        if self._locked:
            raise ConfigurationLockedError(school, year)
        validate(self._problem)
        self.create_calls.append((day_natural_id, name))
        from school_timetable.application.calendar_models import DayWriteResult
        return DayWriteResult(id=day_natural_id, name=name, index=len(self._problem.days))

    def update(self, school, year, day_natural_id, name, validate):
        if self._locked:
            raise ConfigurationLockedError(school, year)
        validate(self._problem)
        self.update_calls.append((day_natural_id, name))
        from school_timetable.application.calendar_models import DayWriteResult
        day = next(d for d in self._problem.days if d.id == day_natural_id)
        return DayWriteResult(id=day_natural_id, name=name, index=day.index)

    def delete(self, school, year, day_natural_id, validate):
        if self._locked:
            raise ConfigurationLockedError(school, year)
        validate(self._problem)
        self.delete_calls.append(day_natural_id)

    def move(self, school, year, day_natural_id, direction, validate):
        if self._locked:
            raise ConfigurationLockedError(school, year)
        validate(self._problem)
        self.move_calls.append((day_natural_id, direction))


class _FakePeriodWritePort:
    def __init__(self, problem, locked: bool = False):
        self._problem = problem
        self._locked = locked
        self.create_calls: list[tuple] = []
        self.update_calls: list[tuple] = []
        self.delete_calls: list[str] = []
        self.move_calls: list[tuple] = []

    def create(self, school, year, period_natural_id, fields, validate):
        if self._locked:
            raise ConfigurationLockedError(school, year)
        validate(self._problem)
        self.create_calls.append((period_natural_id, fields))
        from school_timetable.application.calendar_models import PeriodWriteResult
        return PeriodWriteResult(
            id=period_natural_id, name=fields.name, index=len(self._problem.periods),
            start_time=fields.start_time, end_time=fields.end_time,
            starts_new_block=fields.starts_new_block, is_instructional=True,
        )

    def update(self, school, year, period_natural_id, fields, validate):
        if self._locked:
            raise ConfigurationLockedError(school, year)
        validate(self._problem)
        self.update_calls.append((period_natural_id, fields))
        from school_timetable.application.calendar_models import PeriodWriteResult
        period = next(p for p in self._problem.periods if p.id == period_natural_id)
        return PeriodWriteResult(
            id=period_natural_id, name=fields.name, index=period.index,
            start_time=fields.start_time, end_time=fields.end_time,
            starts_new_block=fields.starts_new_block, is_instructional=period.is_instructional,
        )

    def delete(self, school, year, period_natural_id, validate):
        if self._locked:
            raise ConfigurationLockedError(school, year)
        validate(self._problem)
        self.delete_calls.append(period_natural_id)

    def move(self, school, year, period_natural_id, direction, validate):
        if self._locked:
            raise ConfigurationLockedError(school, year)
        validate(self._problem)
        self.move_calls.append((period_natural_id, direction))


def _fixture_without_time_preferences():
    problem = build_valid_fixture()
    return replace(
        problem,
        teaching_requirements=tuple(replace(r, time_preferences=()) for r in problem.teaching_requirements),
    )


def _service(problem=None, locked=False, day_id_factory=None, period_id_factory=None):
    problem = problem if problem is not None else _fixture_without_time_preferences()
    day_port = _FakeDayWritePort(problem, locked=locked)
    period_port = _FakePeriodWritePort(problem, locked=locked)
    kwargs = {}
    if day_id_factory is not None:
        kwargs["day_id_factory"] = day_id_factory
    if period_id_factory is not None:
        kwargs["period_id_factory"] = period_id_factory
    service = CalendarService(_FakeProblemRepository(problem), day_port, period_port, **kwargs)
    return service, day_port, period_port


# == Day ======================================================================

def test_day_create_succeeds():
    service, day_port, _ = _service()
    result = service.day_create(_SCHOOL, _YEAR, DayFields(name="Saturday"))
    assert result.name == "Saturday"
    assert day_port.create_calls[0][1] == "Saturday"


def test_day_create_trims_whitespace():
    service, day_port, _ = _service()
    service.day_create(_SCHOOL, _YEAR, DayFields(name="  Saturday  "))
    assert day_port.create_calls[0][1] == "Saturday"


def test_day_create_blank_name_rejected():
    service, _, _ = _service()
    with pytest.raises(InvalidDayError) as exc_info:
        service.day_create(_SCHOOL, _YEAR, DayFields(name="   "))
    assert any(e.code == "BLANK_DAY_NAME" for e in exc_info.value.validation_errors)


def test_day_create_duplicate_name_rejected():
    service, _, _ = _service()
    with pytest.raises(DuplicateDayError):
        service.day_create(_SCHOOL, _YEAR, DayFields(name="Monday"))


def test_day_create_generates_day_prefixed_id():
    service, day_port, _ = _service()
    result = service.day_create(_SCHOOL, _YEAR, DayFields(name="Saturday"))
    assert result.id.startswith("day_")
    assert day_port.create_calls[0][0] == result.id


def test_day_create_uses_injected_deterministic_id_factory():
    service, day_port, _ = _service(day_id_factory=lambda: "day_deterministic")
    result = service.day_create(_SCHOOL, _YEAR, DayFields(name="Saturday"))
    assert result.id == "day_deterministic"


def test_day_create_rejected_when_configuration_locked():
    service, _, _ = _service(locked=True)
    with pytest.raises(ConfigurationLockedError):
        service.day_create(_SCHOOL, _YEAR, DayFields(name="Saturday"))


def test_day_update_renames():
    service, day_port, _ = _service()
    result = service.day_update(_SCHOOL, _YEAR, "mon", DayFields(name="Renamed Monday"))
    assert result.id == "mon"
    assert result.name == "Renamed Monday"
    assert day_port.update_calls[0] == ("mon", "Renamed Monday")


def test_day_update_missing_id_rejected():
    service, _, _ = _service()
    with pytest.raises(DayNotFoundError):
        service.day_update(_SCHOOL, _YEAR, "no-such-day", DayFields(name="X"))


def test_day_update_duplicate_other_day_rejected():
    service, _, _ = _service()
    with pytest.raises(DuplicateDayError):
        service.day_update(_SCHOOL, _YEAR, "mon", DayFields(name="Tuesday"))


def test_day_update_same_day_retaining_own_name_allowed():
    service, day_port, _ = _service()
    result = service.day_update(_SCHOOL, _YEAR, "mon", DayFields(name="Monday"))
    assert result.name == "Monday"


def test_day_delete_succeeds_for_unreferenced_day():
    # "fri" is referenced by fixture data (teacher availability, reserved
    # blocks) -- use a synthetic problem with a genuinely unused Day.
    problem = _fixture_without_time_preferences()
    problem = replace(problem, days=problem.days + (Day(id="sat", name="Saturday", index=5),))
    service, day_port, _ = _service(problem=problem)
    service.day_delete(_SCHOOL, _YEAR, "sat")
    assert day_port.delete_calls == ["sat"]


def test_day_delete_missing_rejected():
    service, _, _ = _service()
    with pytest.raises(DayNotFoundError):
        service.day_delete(_SCHOOL, _YEAR, "no-such-day")


def test_day_delete_final_day_blocked():
    problem = _fixture_without_time_preferences()
    single_day_problem = replace(problem, days=(Day(id="only", name="Only Day", index=0),))
    service, _, _ = _service(problem=single_day_problem)
    with pytest.raises(InvalidDayError) as exc_info:
        service.day_delete(_SCHOOL, _YEAR, "only")
    assert any(e.code == "NO_CALENDAR_DAYS" for e in exc_info.value.validation_errors)


def test_day_delete_referenced_by_teacher_availability_blocked():
    service, _, _ = _service()
    with pytest.raises(DayInUseError) as exc_info:
        service.day_delete(_SCHOOL, _YEAR, "tue")
    assert "TEACHER_AVAILABILITY" in exc_info.value.referenced_by


def test_day_delete_rejected_when_configuration_locked():
    # An unreferenced Day, so the service's OWN validate-precheck (which
    # runs before the write port is ever reached) passes -- otherwise
    # DayInUseError would fire first.
    problem = _fixture_without_time_preferences()
    problem = replace(problem, days=problem.days + (Day(id="sat", name="Saturday", index=5),))
    service, _, _ = _service(problem=problem, locked=True)
    with pytest.raises(ConfigurationLockedError):
        service.day_delete(_SCHOOL, _YEAR, "sat")


def test_day_move_up_and_down_succeed():
    service, day_port, _ = _service()
    service.day_move(_SCHOOL, _YEAR, "tue", "up")
    service.day_move(_SCHOOL, _YEAR, "tue", "down")
    assert day_port.move_calls == [("tue", "up"), ("tue", "down")]


def test_day_move_at_top_edge_blocked():
    service, _, _ = _service()
    with pytest.raises(InvalidDayError) as exc_info:
        service.day_move(_SCHOOL, _YEAR, "mon", "up")
    assert any(e.code == "DAY_ALREADY_AT_TOP" for e in exc_info.value.validation_errors)


def test_day_move_at_bottom_edge_blocked():
    service, _, _ = _service()
    with pytest.raises(InvalidDayError) as exc_info:
        service.day_move(_SCHOOL, _YEAR, "fri", "down")
    assert any(e.code == "DAY_ALREADY_AT_BOTTOM" for e in exc_info.value.validation_errors)


def test_day_move_missing_id_rejected():
    service, _, _ = _service()
    with pytest.raises(DayNotFoundError):
        service.day_move(_SCHOOL, _YEAR, "no-such-day", "up")


def test_day_move_rejected_when_configuration_locked():
    service, _, _ = _service(locked=True)
    with pytest.raises(ConfigurationLockedError):
        service.day_move(_SCHOOL, _YEAR, "tue", "up")


# == Period ===================================================================

def test_period_create_succeeds():
    service, _, period_port = _service()
    result = service.period_create(
        _SCHOOL, _YEAR, PeriodFields(name="Period 9", start_time=None, end_time=None, starts_new_block=False),
    )
    assert result.name == "Period 9"
    assert result.is_instructional is True
    assert period_port.create_calls[0][0].startswith("period_")


def test_period_create_with_clock_times_succeeds():
    service, _, period_port = _service()
    result = service.period_create(
        _SCHOOL, _YEAR,
        PeriodFields(name="Period 9", start_time=time(15, 0), end_time=time(15, 45), starts_new_block=True),
    )
    assert result.start_time == time(15, 0)
    assert result.end_time == time(15, 45)
    assert result.starts_new_block is True


def test_period_create_blank_name_rejected():
    service, _, _ = _service()
    with pytest.raises(InvalidPeriodError) as exc_info:
        service.period_create(
            _SCHOOL, _YEAR, PeriodFields(name="  ", start_time=None, end_time=None, starts_new_block=False),
        )
    assert any(e.code == "BLANK_PERIOD_NAME" for e in exc_info.value.validation_errors)


def test_period_create_duplicate_name_rejected():
    service, _, _ = _service()
    with pytest.raises(DuplicatePeriodError):
        service.period_create(
            _SCHOOL, _YEAR, PeriodFields(name="Period 1", start_time=None, end_time=None, starts_new_block=False),
        )


def test_period_create_only_start_time_rejected():
    service, _, _ = _service()
    with pytest.raises(InvalidPeriodError) as exc_info:
        service.period_create(
            _SCHOOL, _YEAR,
            PeriodFields(name="Period 9", start_time=time(9, 0), end_time=None, starts_new_block=False),
        )
    assert any(e.code == "PERIOD_TIME_PAIR_INCOMPLETE" for e in exc_info.value.validation_errors)


def test_period_create_only_end_time_rejected():
    service, _, _ = _service()
    with pytest.raises(InvalidPeriodError) as exc_info:
        service.period_create(
            _SCHOOL, _YEAR,
            PeriodFields(name="Period 9", start_time=None, end_time=time(9, 45), starts_new_block=False),
        )
    assert any(e.code == "PERIOD_TIME_PAIR_INCOMPLETE" for e in exc_info.value.validation_errors)


def test_period_create_equal_start_and_end_time_rejected():
    service, _, _ = _service()
    with pytest.raises(InvalidPeriodError) as exc_info:
        service.period_create(
            _SCHOOL, _YEAR,
            PeriodFields(name="Period 9", start_time=time(9, 0), end_time=time(9, 0), starts_new_block=False),
        )
    assert any(e.code == "PERIOD_TIME_ORDER_INVALID" for e in exc_info.value.validation_errors)


def test_period_create_reversed_start_and_end_time_rejected():
    service, _, _ = _service()
    with pytest.raises(InvalidPeriodError) as exc_info:
        service.period_create(
            _SCHOOL, _YEAR,
            PeriodFields(name="Period 9", start_time=time(10, 0), end_time=time(9, 0), starts_new_block=False),
        )
    assert any(e.code == "PERIOD_TIME_ORDER_INVALID" for e in exc_info.value.validation_errors)


def test_period_create_new_period_is_always_instructional():
    service, _, period_port = _service()
    result = service.period_create(
        _SCHOOL, _YEAR, PeriodFields(name="Period 9", start_time=None, end_time=None, starts_new_block=False),
    )
    assert result.is_instructional is True


def test_period_update_renames_and_preserves_is_instructional():
    service, _, period_port = _service()
    result = service.period_update(
        _SCHOOL, _YEAR, "p1",
        PeriodFields(name="Renamed Period 1", start_time=None, end_time=None, starts_new_block=False),
    )
    assert result.name == "Renamed Period 1"
    assert result.is_instructional is True


def test_period_update_missing_id_rejected():
    service, _, _ = _service()
    with pytest.raises(PeriodNotFoundError):
        service.period_update(
            _SCHOOL, _YEAR, "no-such-period",
            PeriodFields(name="X", start_time=None, end_time=None, starts_new_block=False),
        )


def test_period_update_duplicate_other_period_rejected():
    service, _, _ = _service()
    with pytest.raises(DuplicatePeriodError):
        service.period_update(
            _SCHOOL, _YEAR, "p1",
            PeriodFields(name="Period 2", start_time=None, end_time=None, starts_new_block=False),
        )


def test_period_update_clock_overlap_with_neighbor_rejected():
    service, _, _ = _service()
    # p1 given a start/end that overlaps a would-be-adjacent p2 (both
    # currently null in the fixture, so first give p2 clock times too).
    problem = _fixture_without_time_preferences()
    periods = tuple(
        replace(p, start_time=time(9, 0), end_time=time(9, 45)) if p.id == "p2" else p
        for p in problem.periods
    )
    problem = replace(problem, periods=periods)
    service, _, _ = _service(problem=problem)
    with pytest.raises(InvalidPeriodError) as exc_info:
        service.period_update(
            _SCHOOL, _YEAR, "p1",
            PeriodFields(name="Period 1", start_time=time(9, 15), end_time=time(10, 0), starts_new_block=False),
        )
    assert any(e.code == "PERIOD_CLOCK_TIME_OVERLAP" for e in exc_info.value.validation_errors)


def test_period_delete_succeeds_for_unreferenced_period():
    problem = _fixture_without_time_preferences()
    problem = replace(problem, periods=problem.periods + (
        Period(id="p9", name="Period 9", index=8, block_id="afternoon"),
    ))
    service, _, period_port = _service(problem=problem)
    service.period_delete(_SCHOOL, _YEAR, "p9")
    assert period_port.delete_calls == ["p9"]


def test_period_delete_missing_rejected():
    service, _, _ = _service()
    with pytest.raises(PeriodNotFoundError):
        service.period_delete(_SCHOOL, _YEAR, "no-such-period")


def test_period_delete_final_instructional_period_blocked():
    problem = _fixture_without_time_preferences()
    single_period_problem = replace(
        problem, periods=(Period(id="only", name="Only Period", index=0, block_id="block_0"),),
    )
    service, _, _ = _service(problem=single_period_problem)
    with pytest.raises(InvalidPeriodError) as exc_info:
        service.period_delete(_SCHOOL, _YEAR, "only")
    assert any(e.code == "NO_INSTRUCTIONAL_PERIODS" for e in exc_info.value.validation_errors)


def test_period_delete_referenced_by_teacher_availability_blocked():
    service, _, _ = _service()
    with pytest.raises(PeriodInUseError) as exc_info:
        service.period_delete(_SCHOOL, _YEAR, "p7")
    assert "TEACHER_AVAILABILITY" in exc_info.value.referenced_by


def _problem_with_time_preference_on_index(preferred_index: int):
    """A minimal, otherwise-reference-free 4-Period problem whose sole
    `TeachingRequirement` carries one `TimePreference` naming
    `preferred_index` -- isolates the index-drift-safety rule from every
    other reference kind."""
    problem = _fixture_without_time_preferences()
    requirement = next(r for r in problem.teaching_requirements if r.id == "history_8a")
    updated = replace(requirement, time_preferences=(
        TimePreference(preferred_periods=(preferred_index,), weight=PreferenceWeight.MEDIUM),
    ))
    requirements = tuple(updated if r.id == "history_8a" else r for r in problem.teaching_requirements)
    return replace(problem, teaching_requirements=requirements)


def test_period_delete_referenced_by_time_preference_exact_index_blocked():
    # A TimePreference naming index 0 exactly must block deleting p1
    # (index 0) as TIME_PREFERENCE, regardless of any other reference
    # kind also present for that Period.
    problem = _problem_with_time_preference_on_index(0)
    service, _, _ = _service(problem=problem)
    with pytest.raises(PeriodInUseError) as exc_info:
        service.period_delete(_SCHOOL, _YEAR, "p1")
    assert "TIME_PREFERENCE" in exc_info.value.referenced_by


def test_period_delete_before_time_preference_index_shifts_it_and_is_blocked():
    # Deleting p1 (index 0) would shift p3 (currently index 2, the
    # TimePreference's target) down to index 1, silently redirecting the
    # preference -- unsafe even though p1 itself is never named.
    problem = _problem_with_time_preference_on_index(2)
    service, _, _ = _service(problem=problem)
    with pytest.raises(PeriodInUseError) as exc_info:
        service.period_delete(_SCHOOL, _YEAR, "p1")
    assert "TIME_PREFERENCE" in exc_info.value.referenced_by


def test_period_delete_after_time_preference_index_is_unaffected_and_safe():
    # Deleting p8 (last Period, highest index) can never shift any
    # earlier index -- a TimePreference naming an earlier index (0) is
    # not a hazard for this specific delete.
    problem = _problem_with_time_preference_on_index(0)
    problem = replace(problem, periods=problem.periods + (
        Period(id="p9", name="Period 9", index=8, block_id="afternoon"),
    ))
    service, _, period_port = _service(problem=problem)
    service.period_delete(_SCHOOL, _YEAR, "p9")
    assert period_port.delete_calls == ["p9"]


def test_period_move_up_and_down_succeed_without_time_preferences():
    service, _, period_port = _service()
    service.period_move(_SCHOOL, _YEAR, "p3", "up")
    service.period_move(_SCHOOL, _YEAR, "p3", "down")
    assert period_port.move_calls == [("p3", "up"), ("p3", "down")]


def test_period_move_blocked_when_any_time_preference_exists():
    # The default fixture DOES carry a TimePreference -- reorder of ANY
    # Period (not just the referenced ones) must be blocked, per the
    # deliberately-blanket rule.
    problem = build_valid_fixture()
    service, _, _ = _service(problem=problem)
    with pytest.raises(PeriodReorderBlockedError):
        service.period_move(_SCHOOL, _YEAR, "p8", "up")


def test_period_move_at_top_edge_blocked():
    service, _, _ = _service()
    with pytest.raises(InvalidPeriodError) as exc_info:
        service.period_move(_SCHOOL, _YEAR, "p1", "up")
    assert any(e.code == "PERIOD_ALREADY_AT_TOP" for e in exc_info.value.validation_errors)


def test_period_move_at_bottom_edge_blocked():
    service, _, _ = _service()
    with pytest.raises(InvalidPeriodError) as exc_info:
        service.period_move(_SCHOOL, _YEAR, "p8", "down")
    assert any(e.code == "PERIOD_ALREADY_AT_BOTTOM" for e in exc_info.value.validation_errors)


def test_period_move_missing_id_rejected():
    service, _, _ = _service()
    with pytest.raises(PeriodNotFoundError):
        service.period_move(_SCHOOL, _YEAR, "no-such-period", "up")


def test_period_move_rejected_when_configuration_locked():
    service, _, _ = _service(locked=True)
    with pytest.raises(ConfigurationLockedError):
        service.period_move(_SCHOOL, _YEAR, "p3", "up")
