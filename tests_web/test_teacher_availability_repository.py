"""Real-PostgreSQL integration + concurrency tests for Owner Decision
#38's Teacher Availability write path:
`persistence.teacher_availability_repository.SqlAlchemyTeacherAvailabilityRepository`
and the Owner-Decision-#36 generation-vs-write race closure, now proven
for a Teacher Availability write too (already proven for Teacher/
Class/Subject/Teaching Assignment writes in the sibling
`test_*_repository.py` files).

Everything here is seeded with a real, committed transaction (like
`test_subject_repository.py`, for the same reason: some tests need two
independent, overlapping database transactions), and the `seeded_db`
fixture manually deletes every row it created afterward (cascading
from the one `School` row) regardless of test outcome.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from school_timetable.application.errors import (
    ConfigurationChangedDuringGenerationError,
    ConfigurationLockedError,
    TeacherNotFoundError,
)
from school_timetable.application.teacher_availability_models import TeacherAvailabilityExceptionFields
from school_timetable.application.teacher_availability_rules import validate_replace
from school_timetable.domain.result import SolverStatus
from school_timetable.fixtures.valid_fixture import build_valid_fixture
from school_timetable.persistence import models as m
from school_timetable.persistence.problem_repository import SessionFactorySchedulingProblemRepository
from school_timetable.persistence.schedule_repository import SqlAlchemyScheduleVersionRepository
from school_timetable.persistence.teacher_availability_repository import SqlAlchemyTeacherAvailabilityRepository
from tests_web.support.problem_writer import write_scheduling_problem


@pytest.fixture
def seeded_db(live_db_engine):
    connection = live_db_engine.connect()
    session = Session(bind=connection)
    problem = build_valid_fixture()
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


def _exc(day_id: str, period_id: str, status: str) -> TeacherAvailabilityExceptionFields:
    return TeacherAvailabilityExceptionFields(day_id=day_id, period_id=period_id, status=status)


def _year_id(session, school_id, year_id) -> int:
    return session.execute(
        select(m.AcademicYear.id).join(m.School, m.School.id == m.AcademicYear.school_id).where(
            m.School.natural_id == school_id, m.AcademicYear.natural_id == year_id,
        )
    ).scalar_one()


def _availability_rows(session_factory, school_id, year_id, teacher_natural_id=None):
    """Every persisted `TeacherAvailability` row, resolved to natural
    IDs, as `(teacher_natural_id, day_id, period_id, status, ordinal)`
    tuples -- optionally scoped to one Teacher."""
    session = session_factory()
    try:
        yid = _year_id(session, school_id, year_id)
        query = (
            select(
                m.Teacher.natural_id, m.Day.natural_id, m.Period.natural_id,
                m.TeacherAvailability.status, m.TeacherAvailability.ordinal,
            )
            .join(m.Teacher, m.Teacher.id == m.TeacherAvailability.teacher_id)
            .join(m.Day, m.Day.id == m.TeacherAvailability.day_id)
            .join(m.Period, m.Period.id == m.TeacherAvailability.period_id)
            .where(m.TeacherAvailability.academic_year_id == yid)
        )
        if teacher_natural_id is not None:
            query = query.where(m.Teacher.natural_id == teacher_natural_id)
        return list(session.execute(query).all())
    finally:
        session.close()


def _cell_ordinal(rows, day_id, period_id):
    for _, d, p, _, ordinal in rows:
        if d == day_id and p == period_id:
            return ordinal
    return None


# -- A. write persistence ------------------------------------------------

def test_empty_to_one_unavailable_insert(seeded_db):
    problem, session_factory = seeded_db
    repo = SqlAlchemyTeacherAvailabilityRepository(session_factory)

    def validate(current_problem):
        validate_replace(
            current_problem, problem.school.id, problem.academic_year.id, "t_math",
            exceptions=(_exc("mon", "p5", "UNAVAILABLE"),),
        )

    repo.replace_exceptions(
        problem.school.id, problem.academic_year.id, "t_math", (_exc("mon", "p5", "UNAVAILABLE"),), validate=validate,
    )

    rows = _availability_rows(session_factory, problem.school.id, problem.academic_year.id, "t_math")
    assert [(d, p, s) for _, d, p, s, _ in rows] == [("mon", "p5", "UNAVAILABLE")]


def test_empty_to_one_prefer_not_insert(seeded_db):
    problem, session_factory = seeded_db
    repo = SqlAlchemyTeacherAvailabilityRepository(session_factory)

    def validate(current_problem):
        validate_replace(
            current_problem, problem.school.id, problem.academic_year.id, "t_math",
            exceptions=(_exc("mon", "p5", "PREFER_NOT"),),
        )

    repo.replace_exceptions(
        problem.school.id, problem.academic_year.id, "t_math", (_exc("mon", "p5", "PREFER_NOT"),), validate=validate,
    )

    rows = _availability_rows(session_factory, problem.school.id, problem.academic_year.id, "t_math")
    assert [(d, p, s) for _, d, p, s, _ in rows] == [("mon", "p5", "PREFER_NOT")]


def test_mixed_inserts(seeded_db):
    problem, session_factory = seeded_db
    repo = SqlAlchemyTeacherAvailabilityRepository(session_factory)
    desired = (_exc("mon", "p5", "UNAVAILABLE"), _exc("tue", "p6", "PREFER_NOT"))

    def validate(current_problem):
        validate_replace(current_problem, problem.school.id, problem.academic_year.id, "t_math", exceptions=desired)

    repo.replace_exceptions(problem.school.id, problem.academic_year.id, "t_math", desired, validate=validate)

    rows = _availability_rows(session_factory, problem.school.id, problem.academic_year.id, "t_math")
    assert {(d, p, s) for _, d, p, s, _ in rows} == {("mon", "p5", "UNAVAILABLE"), ("tue", "p6", "PREFER_NOT")}


def test_next_ordinal_uses_global_ay_max_not_selected_teacher_max(seeded_db):
    """The fixture already has 3 existing TeacherAvailability rows
    (t_science UNAVAILABLE x2, t_history PREFER_NOT) -- ordinals 0-2.
    A brand-new exception for a different teacher (t_math, with none
    of its own) must still receive ordinal 3, computed across the
    whole AcademicYear, never restarting at 0 for a teacher with no
    rows."""
    problem, session_factory = seeded_db
    repo = SqlAlchemyTeacherAvailabilityRepository(session_factory)

    def validate(current_problem):
        validate_replace(
            current_problem, problem.school.id, problem.academic_year.id, "t_math",
            exceptions=(_exc("mon", "p5", "UNAVAILABLE"),),
        )

    repo.replace_exceptions(
        problem.school.id, problem.academic_year.id, "t_math", (_exc("mon", "p5", "UNAVAILABLE"),), validate=validate,
    )

    all_rows = _availability_rows(session_factory, problem.school.id, problem.academic_year.id)
    new_ordinal = _cell_ordinal(all_rows, "mon", "p5")
    assert new_ordinal == 3


def test_new_row_ordinal_assigned_deterministically_by_day_then_period_index(seeded_db):
    problem, session_factory = seeded_db
    repo = SqlAlchemyTeacherAvailabilityRepository(session_factory)
    # Request them out of calendar order -- the assigned ordinals must
    # still follow (Day.index, Period.index), never request order.
    desired = (_exc("wed", "p1", "UNAVAILABLE"), _exc("mon", "p2", "UNAVAILABLE"), _exc("mon", "p1", "PREFER_NOT"))

    def validate(current_problem):
        validate_replace(current_problem, problem.school.id, problem.academic_year.id, "t_math", exceptions=desired)

    repo.replace_exceptions(problem.school.id, problem.academic_year.id, "t_math", desired, validate=validate)

    rows = _availability_rows(session_factory, problem.school.id, problem.academic_year.id, "t_math")
    ordinal_mon_p1 = _cell_ordinal(rows, "mon", "p1")
    ordinal_mon_p2 = _cell_ordinal(rows, "mon", "p2")
    ordinal_wed_p1 = _cell_ordinal(rows, "wed", "p1")
    assert ordinal_mon_p1 < ordinal_mon_p2 < ordinal_wed_p1


def test_unchanged_cell_preserves_ordinal(seeded_db):
    problem, session_factory = seeded_db
    repo = SqlAlchemyTeacherAvailabilityRepository(session_factory)

    before_rows = _availability_rows(session_factory, problem.school.id, problem.academic_year.id, "t_science")
    before_ordinal = _cell_ordinal(before_rows, "fri", "p7")
    assert before_ordinal is not None

    # Replace t_science's set with the exact same cell, unchanged, plus nothing else.
    desired = (_exc("fri", "p7", "UNAVAILABLE"), _exc("fri", "p8", "UNAVAILABLE"))

    def validate(current_problem):
        validate_replace(current_problem, problem.school.id, problem.academic_year.id, "t_science", exceptions=desired)

    repo.replace_exceptions(problem.school.id, problem.academic_year.id, "t_science", desired, validate=validate)

    after_rows = _availability_rows(session_factory, problem.school.id, problem.academic_year.id, "t_science")
    assert _cell_ordinal(after_rows, "fri", "p7") == before_ordinal


def test_status_change_preserves_ordinal(seeded_db):
    problem, session_factory = seeded_db
    repo = SqlAlchemyTeacherAvailabilityRepository(session_factory)

    before_rows = _availability_rows(session_factory, problem.school.id, problem.academic_year.id, "t_science")
    before_ordinal = _cell_ordinal(before_rows, "fri", "p7")

    desired = (_exc("fri", "p7", "PREFER_NOT"), _exc("fri", "p8", "UNAVAILABLE"))

    def validate(current_problem):
        validate_replace(current_problem, problem.school.id, problem.academic_year.id, "t_science", exceptions=desired)

    repo.replace_exceptions(problem.school.id, problem.academic_year.id, "t_science", desired, validate=validate)

    after_rows = _availability_rows(session_factory, problem.school.id, problem.academic_year.id, "t_science")
    assert [(d, p, s) for _, d, p, s, _ in after_rows if (d, p) == ("fri", "p7")] == [("fri", "p7", "PREFER_NOT")]
    assert _cell_ordinal(after_rows, "fri", "p7") == before_ordinal


def test_removed_cell_deleted_surviving_cell_untouched(seeded_db):
    problem, session_factory = seeded_db
    repo = SqlAlchemyTeacherAvailabilityRepository(session_factory)

    # t_science starts with {(fri,p7): UNAVAILABLE, (fri,p8): UNAVAILABLE}.
    desired = (_exc("fri", "p7", "UNAVAILABLE"),)

    def validate(current_problem):
        validate_replace(current_problem, problem.school.id, problem.academic_year.id, "t_science", exceptions=desired)

    repo.replace_exceptions(problem.school.id, problem.academic_year.id, "t_science", desired, validate=validate)

    rows = _availability_rows(session_factory, problem.school.id, problem.academic_year.id, "t_science")
    assert [(d, p, s) for _, d, p, s, _ in rows] == [("fri", "p7", "UNAVAILABLE")]


def test_empty_replacement_clears_selected_teacher_only(seeded_db):
    problem, session_factory = seeded_db
    repo = SqlAlchemyTeacherAvailabilityRepository(session_factory)

    def validate(current_problem):
        validate_replace(current_problem, problem.school.id, problem.academic_year.id, "t_science", exceptions=())

    repo.replace_exceptions(problem.school.id, problem.academic_year.id, "t_science", (), validate=validate)

    science_rows = _availability_rows(session_factory, problem.school.id, problem.academic_year.id, "t_science")
    assert science_rows == []

    history_rows = _availability_rows(session_factory, problem.school.id, problem.academic_year.id, "t_history")
    assert [(d, p, s) for _, d, p, s, _ in history_rows] == [("tue", "p3", "PREFER_NOT")]


# -- B. atomicity ---------------------------------------------------------

def test_validation_failure_leaves_zero_db_changes(seeded_db):
    problem, session_factory = seeded_db
    repo = SqlAlchemyTeacherAvailabilityRepository(session_factory)
    before = _availability_rows(session_factory, problem.school.id, problem.academic_year.id)

    def failing_validate(current_problem):
        raise TeacherNotFoundError(problem.school.id, problem.academic_year.id, "t_math")

    with pytest.raises(TeacherNotFoundError):
        repo.replace_exceptions(
            problem.school.id, problem.academic_year.id, "t_math",
            (_exc("mon", "p5", "UNAVAILABLE"),), validate=failing_validate,
        )

    after = _availability_rows(session_factory, problem.school.id, problem.academic_year.id)
    assert after == before


def test_unknown_teacher_raises_and_makes_zero_db_changes(seeded_db):
    """Defense-in-depth: even with a deliberately permissive `validate`
    callback that never checks the Teacher exists, the repository's
    own resolved-row lookup still raises `TeacherNotFoundError` and
    performs no mutation."""
    problem, session_factory = seeded_db
    repo = SqlAlchemyTeacherAvailabilityRepository(session_factory)
    before = _availability_rows(session_factory, problem.school.id, problem.academic_year.id)

    def permissive_validate(current_problem):
        pass

    with pytest.raises(TeacherNotFoundError):
        repo.replace_exceptions(
            problem.school.id, problem.academic_year.id, "t_nobody",
            (_exc("mon", "p5", "UNAVAILABLE"),), validate=permissive_validate,
        )

    after = _availability_rows(session_factory, problem.school.id, problem.academic_year.id)
    assert after == before


def test_lock_rejection_leaves_zero_db_changes(seeded_db):
    problem, session_factory = seeded_db
    repo = SqlAlchemyTeacherAvailabilityRepository(session_factory)
    schedule_repo = SqlAlchemyScheduleVersionRepository(session_factory)

    loaded = SessionFactorySchedulingProblemRepository(session_factory).load_by_school_and_year(
        problem.school.id, problem.academic_year.id,
    )
    schedule_repo.persist_initial_version(
        problem.school.id, problem.academic_year.id, loaded,
        entries=(), solver_status=SolverStatus.OPTIMAL, total_soft_penalty=0,
        wall_time_seconds=0.01, random_seed=None,
    )
    before = _availability_rows(session_factory, problem.school.id, problem.academic_year.id)

    def validate(current_problem):
        validate_replace(
            current_problem, problem.school.id, problem.academic_year.id, "t_math",
            exceptions=(_exc("mon", "p5", "UNAVAILABLE"),),
        )

    with pytest.raises(ConfigurationLockedError):
        repo.replace_exceptions(
            problem.school.id, problem.academic_year.id, "t_math",
            (_exc("mon", "p5", "UNAVAILABLE"),), validate=validate,
        )

    after = _availability_rows(session_factory, problem.school.id, problem.academic_year.id)
    assert after == before


# -- C. generation-vs-write race (Owner Decision #36) --------------------

def test_generation_persist_aborts_when_availability_write_committed_since_load(seeded_db, live_db_engine):
    """Sequential, fully deterministic simulation of: (1) generation
    loads problem P; (2) a Teacher Availability write commits; (3)
    generation attempts to persist against the now-stale P.
    `SchedulingProblem.teacher_availabilities` is itself part of the
    frozen-dataclass equality the generation persist step compares."""
    problem, session_factory = seeded_db
    schedule_repo = SqlAlchemyScheduleVersionRepository(session_factory)
    availability_repo = SqlAlchemyTeacherAvailabilityRepository(session_factory)

    stale_problem = SessionFactorySchedulingProblemRepository(session_factory).load_by_school_and_year(
        problem.school.id, problem.academic_year.id,
    )

    def validate(current_problem):
        validate_replace(
            current_problem, problem.school.id, problem.academic_year.id, "t_math",
            exceptions=(_exc("mon", "p5", "UNAVAILABLE"),),
        )

    availability_repo.replace_exceptions(
        problem.school.id, problem.academic_year.id, "t_math",
        (_exc("mon", "p5", "UNAVAILABLE"),), validate=validate,
    )

    with pytest.raises(ConfigurationChangedDuringGenerationError):
        schedule_repo.persist_initial_version(
            problem.school.id, problem.academic_year.id, stale_problem,
            entries=(), solver_status=SolverStatus.OPTIMAL, total_soft_penalty=0,
            wall_time_seconds=0.01, random_seed=None,
        )

    connection = live_db_engine.connect()
    session = Session(bind=connection)
    year_id = _year_id(session, problem.school.id, problem.academic_year.id)
    schedule_count = len(
        session.execute(select(m.Schedule).where(m.Schedule.academic_year_id == year_id)).scalars().all()
    )
    session.close()
    connection.close()
    assert schedule_count == 0


def test_generation_persist_succeeds_then_blocks_a_waiting_availability_write(seeded_db):
    """Opposite ordering: generation reaches the final locked persist
    first and commits; a Teacher Availability write attempted
    afterward must see the now-generated schedule and be rejected
    under Decision #35, with the configuration left unchanged."""
    problem, session_factory = seeded_db
    schedule_repo = SqlAlchemyScheduleVersionRepository(session_factory)
    availability_repo = SqlAlchemyTeacherAvailabilityRepository(session_factory)

    loaded_problem = SessionFactorySchedulingProblemRepository(session_factory).load_by_school_and_year(
        problem.school.id, problem.academic_year.id,
    )

    schedule_repo.persist_initial_version(
        problem.school.id, problem.academic_year.id, loaded_problem,
        entries=(), solver_status=SolverStatus.OPTIMAL, total_soft_penalty=0,
        wall_time_seconds=0.01, random_seed=None,
    )

    def validate(current_problem):
        validate_replace(
            current_problem, problem.school.id, problem.academic_year.id, "t_math",
            exceptions=(_exc("mon", "p5", "UNAVAILABLE"),),
        )

    with pytest.raises(ConfigurationLockedError):
        availability_repo.replace_exceptions(
            problem.school.id, problem.academic_year.id, "t_math",
            (_exc("mon", "p5", "UNAVAILABLE"),), validate=validate,
        )

    after_rows = _availability_rows(session_factory, problem.school.id, problem.academic_year.id, "t_math")
    assert after_rows == []
