"""Real-PostgreSQL integration + concurrency tests for Calendar A's
Day/Period catalog write path:
`persistence.calendar_repository.SqlAlchemyCalendarDayRepository`/
`SqlAlchemyCalendarPeriodRepository`, and the Owner-Decision-#36
generation-vs-write race closure, proven the same way as every sibling
`test_*_repository.py` in this suite.

Everything here is seeded with a real, committed transaction (like
`test_resource_repository.py`, for the same reason: some tests need two
independent, overlapping database transactions), and the `seeded_db`
fixture manually deletes every row it created afterward (cascading from
the one `School` row) regardless of test outcome.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import time

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from school_timetable.application import calendar_rules as rules
from school_timetable.application.calendar_models import PeriodFields
from school_timetable.application.errors import (
    ConfigurationChangedDuringGenerationError,
    ConfigurationLockedError,
    DayInUseError,
    DayNotFoundError,
    InvalidPeriodError,
    PeriodInUseError,
    PeriodNotFoundError,
    PeriodReorderBlockedError,
)
from school_timetable.domain.calendar import Day, Period, derive_starts_new_block
from school_timetable.domain.result import SolverStatus
from school_timetable.fixtures.valid_fixture import build_valid_fixture
from school_timetable.persistence import models as m
from school_timetable.persistence.calendar_repository import (
    SqlAlchemyCalendarDayRepository,
    SqlAlchemyCalendarPeriodRepository,
)
from school_timetable.persistence.problem_repository import SessionFactorySchedulingProblemRepository
from school_timetable.persistence.schedule_repository import SqlAlchemyScheduleVersionRepository
from tests_web.support.problem_writer import write_scheduling_problem


def _fixture_without_time_preferences():
    problem = build_valid_fixture()
    return replace(
        problem,
        teaching_requirements=tuple(replace(r, time_preferences=()) for r in problem.teaching_requirements),
    )


@pytest.fixture
def seeded_db(live_db_engine):
    connection = live_db_engine.connect()
    session = Session(bind=connection)
    problem = _fixture_without_time_preferences()
    write_scheduling_problem(session, problem)
    session.commit()
    session.close()
    connection.close()

    session_factory = sessionmaker(bind=live_db_engine, autoflush=False, autocommit=False, expire_on_commit=False)

    try:
        yield problem, session_factory
    finally:
        cleanup_connection = live_db_engine.connect()
        cleanup_session = Session(bind=cleanup_connection)
        school_row = cleanup_session.execute(
            select(m.School).where(m.School.natural_id == problem.school.id)
        ).scalar_one_or_none()
        if school_row is not None:
            cleanup_session.delete(school_row)  # DB-level ON DELETE CASCADE removes everything under it
            cleanup_session.commit()
        cleanup_session.close()
        cleanup_connection.close()


def _reload(session_factory, school_id, year_id):
    return SessionFactorySchedulingProblemRepository(session_factory).load_by_school_and_year(school_id, year_id)


def _year_id(session_factory, school_id, year_id):
    session = session_factory()
    try:
        return session.execute(
            select(m.AcademicYear.id).join(m.School, m.School.id == m.AcademicYear.school_id).where(
                m.School.natural_id == school_id, m.AcademicYear.natural_id == year_id,
            )
        ).scalar_one()
    finally:
        session.close()


# == Day ======================================================================

def test_day_create_round_trip(seeded_db):
    problem, session_factory = seeded_db
    repo = SqlAlchemyCalendarDayRepository(session_factory)

    def validate(current_problem):
        rules.validate_day_create(current_problem, problem.school.id, problem.academic_year.id, name="Saturday")

    result = repo.create(problem.school.id, problem.academic_year.id, "day_sat", "Saturday", validate=validate)
    assert result.index == len(problem.days)

    reloaded = _reload(session_factory, problem.school.id, problem.academic_year.id)
    created = next(d for d in reloaded.days if d.id == "day_sat")
    assert created.name == "Saturday"
    assert created.index == len(problem.days)


def test_day_create_appends_and_indexes_stay_contiguous(seeded_db):
    problem, session_factory = seeded_db
    repo = SqlAlchemyCalendarDayRepository(session_factory)

    def validate(current_problem):
        rules.validate_day_create(current_problem, problem.school.id, problem.academic_year.id, name="Saturday")

    repo.create(problem.school.id, problem.academic_year.id, "day_sat", "Saturday", validate=validate)

    reloaded = _reload(session_factory, problem.school.id, problem.academic_year.id)
    indexes = sorted(d.index for d in reloaded.days)
    assert indexes == list(range(len(reloaded.days)))


def test_day_create_validation_failure_leaves_no_partial_mutation(seeded_db):
    problem, session_factory = seeded_db
    repo = SqlAlchemyCalendarDayRepository(session_factory)

    def failing_validate(current_problem):
        raise DayNotFoundError(problem.school.id, problem.academic_year.id, "irrelevant")

    with pytest.raises(DayNotFoundError):
        repo.create(problem.school.id, problem.academic_year.id, "day_should_not_exist", "Saturday",
                    validate=failing_validate)

    reloaded = _reload(session_factory, problem.school.id, problem.academic_year.id)
    assert "day_should_not_exist" not in {d.id for d in reloaded.days}


def test_day_update_round_trip(seeded_db):
    problem, session_factory = seeded_db
    repo = SqlAlchemyCalendarDayRepository(session_factory)

    def validate(current_problem):
        rules.validate_day_update(current_problem, problem.school.id, problem.academic_year.id, "mon", name="Renamed Monday")

    result = repo.update(problem.school.id, problem.academic_year.id, "mon", "Renamed Monday", validate=validate)
    assert result.name == "Renamed Monday"

    reloaded = _reload(session_factory, problem.school.id, problem.academic_year.id)
    updated = next(d for d in reloaded.days if d.id == "mon")
    assert updated.name == "Renamed Monday"
    assert updated.index == 0


def test_day_delete_round_trip_and_reindexes(seeded_db):
    problem, session_factory = seeded_db
    repo = SqlAlchemyCalendarDayRepository(session_factory)

    def validate_c(current_problem):
        rules.validate_day_create(current_problem, problem.school.id, problem.academic_year.id, name="Saturday")

    repo.create(problem.school.id, problem.academic_year.id, "day_sat", "Saturday", validate=validate_c)

    # "day_sat" is the newly-created, genuinely unreferenced Day -- "tue"
    # is referenced by the fixture's own PREFER_NOT TeacherAvailability
    # row and is exercised separately by the blocker test below.
    def validate_d(current_problem):
        rules.validate_day_delete(current_problem, problem.school.id, problem.academic_year.id, "day_sat")

    repo.delete(problem.school.id, problem.academic_year.id, "day_sat", validate=validate_d)

    reloaded = _reload(session_factory, problem.school.id, problem.academic_year.id)
    ids = [d.id for d in sorted(reloaded.days, key=lambda d: d.index)]
    assert "day_sat" not in ids
    indexes = [d.index for d in reloaded.days]
    assert sorted(indexes) == list(range(len(reloaded.days)))
    # Relative order of the remaining Days is preserved.
    assert ids == ["mon", "tue", "wed", "thu", "fri"]


def test_day_delete_blocker_leaves_nothing_deleted(seeded_db):
    problem, session_factory = seeded_db
    repo = SqlAlchemyCalendarDayRepository(session_factory)

    def validate(current_problem):
        rules.validate_day_delete(current_problem, problem.school.id, problem.academic_year.id, "tue")

    # "tue" is referenced by a TeacherAvailability PREFER_NOT row in the fixture.
    with pytest.raises(DayInUseError):
        repo.delete(problem.school.id, problem.academic_year.id, "tue", validate=validate)

    reloaded = _reload(session_factory, problem.school.id, problem.academic_year.id)
    assert "tue" in {d.id for d in reloaded.days}


def test_day_move_swaps_index_atomically(seeded_db):
    problem, session_factory = seeded_db
    repo = SqlAlchemyCalendarDayRepository(session_factory)

    def validate(current_problem):
        rules.validate_day_move(current_problem, problem.school.id, problem.academic_year.id, "tue", "up")

    repo.move(problem.school.id, problem.academic_year.id, "tue", "up", validate=validate)

    reloaded = _reload(session_factory, problem.school.id, problem.academic_year.id)
    by_id = {d.id: d.index for d in reloaded.days}
    assert by_id["tue"] == 0
    assert by_id["mon"] == 1
    indexes = sorted(by_id.values())
    assert indexes == list(range(len(by_id)))


def test_day_move_validation_failure_leaves_order_unchanged(seeded_db):
    problem, session_factory = seeded_db
    repo = SqlAlchemyCalendarDayRepository(session_factory)

    def failing_validate(current_problem):
        rules.validate_day_move(current_problem, problem.school.id, problem.academic_year.id, "mon", "up")

    with pytest.raises(Exception):
        repo.move(problem.school.id, problem.academic_year.id, "mon", "up", validate=failing_validate)

    reloaded = _reload(session_factory, problem.school.id, problem.academic_year.id)
    by_id = {d.id: d.index for d in reloaded.days}
    assert by_id["mon"] == 0


# == Period ===================================================================

def test_period_create_round_trip_with_clock_times(seeded_db):
    problem, session_factory = seeded_db
    repo = SqlAlchemyCalendarPeriodRepository(session_factory)
    fields = PeriodFields(name="Period 9", start_time=time(15, 0), end_time=time(15, 45), starts_new_block=True)

    def validate(current_problem):
        candidate = current_problem.periods + (
            Period(id="period_9", name="Period 9", index=len(current_problem.periods), block_id="",
                   start_time=fields.start_time, end_time=fields.end_time),
        )
        rules.validate_period_create(
            current_problem, problem.school.id, problem.academic_year.id, name="Period 9",
            start_time=fields.start_time, end_time=fields.end_time, candidate_periods=candidate,
        )

    result = repo.create(problem.school.id, problem.academic_year.id, "period_9", fields, validate=validate)
    assert result.start_time == time(15, 0)
    assert result.starts_new_block is True
    assert result.is_instructional is True

    reloaded = _reload(session_factory, problem.school.id, problem.academic_year.id)
    created = next(p for p in reloaded.periods if p.id == "period_9")
    assert created.start_time == time(15, 0)
    assert created.end_time == time(15, 45)


def test_period_create_recomputes_block_ids_for_whole_sequence(seeded_db):
    problem, session_factory = seeded_db
    repo = SqlAlchemyCalendarPeriodRepository(session_factory)
    fields = PeriodFields(name="Period 9", start_time=None, end_time=None, starts_new_block=True)

    def validate(current_problem):
        candidate = current_problem.periods + (
            Period(id="period_9", name="Period 9", index=len(current_problem.periods), block_id=""),
        )
        rules.validate_period_create(
            current_problem, problem.school.id, problem.academic_year.id, name="Period 9",
            start_time=None, end_time=None, candidate_periods=candidate,
        )

    repo.create(problem.school.id, problem.academic_year.id, "period_9", fields, validate=validate)

    reloaded = _reload(session_factory, problem.school.id, problem.academic_year.id)
    markers = derive_starts_new_block(reloaded.periods)
    assert markers["period_9"] is True
    by_id = {p.id: p for p in reloaded.periods}
    # p1..p4 morning, p5..p8 afternoon, period_9 starts its own new block.
    assert by_id["p1"].block_id == by_id["p4"].block_id
    assert by_id["p5"].block_id == by_id["p8"].block_id
    assert by_id["period_9"].block_id not in (by_id["p1"].block_id, by_id["p5"].block_id)


def test_period_create_new_period_is_always_instructional(seeded_db):
    problem, session_factory = seeded_db
    repo = SqlAlchemyCalendarPeriodRepository(session_factory)
    fields = PeriodFields(name="Period 9", start_time=None, end_time=None, starts_new_block=False)

    def validate(current_problem):
        candidate = current_problem.periods + (
            Period(id="period_9", name="Period 9", index=len(current_problem.periods), block_id=""),
        )
        rules.validate_period_create(
            current_problem, problem.school.id, problem.academic_year.id, name="Period 9",
            start_time=None, end_time=None, candidate_periods=candidate,
        )

    result = repo.create(problem.school.id, problem.academic_year.id, "period_9", fields, validate=validate)
    assert result.is_instructional is True


def test_period_update_preserves_legacy_is_instructional_false(seeded_db, live_db_engine):
    """A legacy `is_instructional=False` row's flag must survive a
    Calendar A write untouched -- `PeriodFields`/`PeriodWriteRequest`
    have no way to set it, and the repository never assigns it on
    update."""
    problem, session_factory = seeded_db
    year_id = _year_id(session_factory, problem.school.id, problem.academic_year.id)

    connection = live_db_engine.connect()
    session = Session(bind=connection)
    revision_id = session.get(m.AcademicYear, year_id).draft_revision_id
    session.add(m.Period(
        academic_year_id=year_id, configuration_revision_id=revision_id,
        natural_id="p_break", name="Break", idx=8,
        block_id="legacy_break", is_instructional=False,
    ))
    session.commit()
    session.close()
    connection.close()

    repo = SqlAlchemyCalendarPeriodRepository(session_factory)
    fields = PeriodFields(name="Break Renamed", start_time=None, end_time=None, starts_new_block=True)

    def validate(current_problem):
        candidate = tuple(
            replace(p, name="Break Renamed") if p.id == "p_break" else p for p in current_problem.periods
        )
        rules.validate_period_update(
            current_problem, problem.school.id, problem.academic_year.id, "p_break", name="Break Renamed",
            start_time=None, end_time=None, candidate_periods=candidate,
        )

    result = repo.update(problem.school.id, problem.academic_year.id, "p_break", fields, validate=validate)
    assert result.is_instructional is False

    reloaded = _reload(session_factory, problem.school.id, problem.academic_year.id)
    updated = next(p for p in reloaded.periods if p.id == "p_break")
    assert updated.is_instructional is False
    assert updated.name == "Break Renamed"


def test_period_delete_round_trip_reindexes_and_recomputes_blocks(seeded_db):
    problem, session_factory = seeded_db
    repo = SqlAlchemyCalendarPeriodRepository(session_factory)

    def validate(current_problem):
        rules.validate_period_delete(current_problem, problem.school.id, problem.academic_year.id, "p4")

    repo.delete(problem.school.id, problem.academic_year.id, "p4", validate=validate)

    reloaded = _reload(session_factory, problem.school.id, problem.academic_year.id)
    ids_in_order = [p.id for p in sorted(reloaded.periods, key=lambda p: p.index)]
    assert ids_in_order == ["p1", "p2", "p3", "p5", "p6", "p7", "p8"]
    indexes = [p.index for p in reloaded.periods]
    assert sorted(indexes) == list(range(len(indexes)))


def test_period_delete_final_instructional_period_blocked(live_db_engine):
    # A dedicated, minimal, reference-free problem (rather than pruning
    # the shared 8-Period fixture down to one, which would require also
    # stripping every ReservedBlockSlot/FixedPlacement FK reference) --
    # isolates the minimum-calendar rule from every other reference kind.
    from school_timetable.domain.calendar import AcademicYear, Day as DomainDay
    from school_timetable.domain.problem import SchedulingProblem
    from school_timetable.domain.school import School

    problem = SchedulingProblem(
        school=School(id="minimal-school", name="Minimal School"),
        academic_year=AcademicYear(id="minimal-year", label="Minimal Year"),
        days=(DomainDay(id="only_day", name="Only Day", index=0),),
        periods=(Period(id="only_period", name="Only Period", index=0, block_id="block_0"),),
        teachers=(), class_sections=(), participant_groups=(), activities=(),
        teaching_requirements=(),
    )

    connection = live_db_engine.connect()
    session = Session(bind=connection)
    write_scheduling_problem(session, problem)
    session.commit()
    session.close()
    connection.close()

    session_factory = sessionmaker(bind=live_db_engine, autoflush=False, autocommit=False, expire_on_commit=False)
    try:
        repo = SqlAlchemyCalendarPeriodRepository(session_factory)

        def validate(current_problem):
            rules.validate_period_delete(current_problem, problem.school.id, problem.academic_year.id, "only_period")

        with pytest.raises(InvalidPeriodError) as exc_info:
            repo.delete(problem.school.id, problem.academic_year.id, "only_period", validate=validate)
        assert any(e.code == "NO_INSTRUCTIONAL_PERIODS" for e in exc_info.value.validation_errors)

        reloaded = _reload(session_factory, problem.school.id, problem.academic_year.id)
        assert "only_period" in {p.id for p in reloaded.periods}
    finally:
        cleanup_connection = live_db_engine.connect()
        cleanup_session = Session(bind=cleanup_connection)
        school_row = cleanup_session.execute(
            select(m.School).where(m.School.natural_id == "minimal-school")
        ).scalar_one_or_none()
        if school_row is not None:
            cleanup_session.delete(school_row)
            cleanup_session.commit()
        cleanup_session.close()
        cleanup_connection.close()


def test_period_move_swaps_and_recomputes_block_ids(seeded_db):
    problem, session_factory = seeded_db
    repo = SqlAlchemyCalendarPeriodRepository(session_factory)

    def validate(current_problem):
        rules.validate_period_move(current_problem, problem.school.id, problem.academic_year.id, "p4", "down")

    repo.move(problem.school.id, problem.academic_year.id, "p4", "down", validate=validate)

    reloaded = _reload(session_factory, problem.school.id, problem.academic_year.id)
    by_id = {p.id: p for p in reloaded.periods}
    assert by_id["p4"].index == 4
    assert by_id["p5"].index == 3
    indexes = sorted(p.index for p in reloaded.periods)
    assert indexes == list(range(len(indexes)))
    # p4's own starts_new_block marker (False) travels with it; p1 stays
    # the sole block-0 starter regardless of the swap.
    markers = derive_starts_new_block(reloaded.periods)
    assert markers["p1"] is True


def test_period_move_blocked_when_time_preference_exists(seeded_db):
    problem, session_factory = seeded_db
    year_id = _year_id(session_factory, problem.school.id, problem.academic_year.id)
    repo = SqlAlchemyCalendarPeriodRepository(session_factory)

    # Insert one TimePreference row via a real ORM write to prove the
    # repository's own `validate` recheck (invoked under the lock)
    # genuinely rejects, not just the pre-check in CalendarService.
    from school_timetable.persistence import models as orm
    connection_setup = None
    session_setup = session_factory()
    try:
        requirement = session_setup.execute(
            select(orm.TeachingRequirement).where(orm.TeachingRequirement.academic_year_id == year_id).limit(1)
        ).scalar_one()
        revision_id = session_setup.get(orm.AcademicYear, year_id).draft_revision_id
        session_setup.add(orm.TimePreference(
            academic_year_id=year_id, configuration_revision_id=revision_id, teaching_requirement_id=requirement.id,
            preferred_period_indexes=[0], weight="MEDIUM", ordinal=0,
        ))
        session_setup.commit()
    finally:
        session_setup.close()

    def validate(current_problem):
        rules.validate_period_move(current_problem, problem.school.id, problem.academic_year.id, "p4", "down")

    with pytest.raises(PeriodReorderBlockedError):
        repo.move(problem.school.id, problem.academic_year.id, "p4", "down", validate=validate)


# == generation-vs-write race (Owner Decision #36) ===========================

def test_generation_persist_aborts_when_period_write_committed_since_load(seeded_db):
    problem, session_factory = seeded_db
    schedule_repo = SqlAlchemyScheduleVersionRepository(session_factory)
    period_repo = SqlAlchemyCalendarPeriodRepository(session_factory)

    stale_problem = _reload(session_factory, problem.school.id, problem.academic_year.id)

    fields = PeriodFields(name="Renamed Period 1", start_time=None, end_time=None, starts_new_block=True)

    def validate(current_problem):
        candidate = tuple(
            replace(p, name="Renamed Period 1") if p.id == "p1" else p for p in current_problem.periods
        )
        rules.validate_period_update(
            current_problem, problem.school.id, problem.academic_year.id, "p1", name="Renamed Period 1",
            start_time=None, end_time=None, candidate_periods=candidate,
        )

    period_repo.update(problem.school.id, problem.academic_year.id, "p1", fields, validate=validate)

    with pytest.raises(ConfigurationChangedDuringGenerationError):
        schedule_repo.persist_initial_version(
            problem.school.id, problem.academic_year.id, stale_problem,
            entries=(), solver_status=SolverStatus.OPTIMAL, total_soft_penalty=0,
            wall_time_seconds=0.01, random_seed=None,
        )


def test_generation_persist_succeeds_then_blocks_a_waiting_day_write(seeded_db):
    problem, session_factory = seeded_db
    schedule_repo = SqlAlchemyScheduleVersionRepository(session_factory)
    day_repo = SqlAlchemyCalendarDayRepository(session_factory)

    loaded_problem = _reload(session_factory, problem.school.id, problem.academic_year.id)

    schedule_repo.persist_initial_version(
        problem.school.id, problem.academic_year.id, loaded_problem,
        entries=(), solver_status=SolverStatus.OPTIMAL, total_soft_penalty=0,
        wall_time_seconds=0.01, random_seed=None,
    )

    def validate(current_problem):
        rules.validate_day_create(current_problem, problem.school.id, problem.academic_year.id, name="Saturday")

    with pytest.raises(ConfigurationLockedError):
        day_repo.create(problem.school.id, problem.academic_year.id, "day_sat", "Saturday", validate=validate)

    reloaded = _reload(session_factory, problem.school.id, problem.academic_year.id)
    assert "day_sat" not in {d.id for d in reloaded.days}
