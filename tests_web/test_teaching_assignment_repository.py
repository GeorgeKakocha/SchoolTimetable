"""Real-PostgreSQL integration + concurrency tests for the Phase 3C.2
Teaching Assignment write path (`docs/DECISIONS.md` #34, #36):
`persistence.teaching_assignment_repository.SqlAlchemyTeachingAssignmentRepository`
and the Owner-Decision-#36 generation-vs-write race closure inside
`SqlAlchemyScheduleVersionRepository.persist_initial_version`.

The duplicate-concurrency test genuinely needs two independent,
overlapping database transactions (to prove the `AcademicYear` row
lock actually serializes them, not merely that a later read sees an
earlier commit) -- so, unlike every other `tests_web` suite, this file
does NOT use the SAVEPOINT-nested-in-one-outer-rolled-back-transaction
pattern (`tests_web/test_schedule_repository.py`'s `db` fixture):
everything here is seeded with a real, committed transaction, and the
`seeded_db` fixture manually deletes every row it created afterward
(cascading from the one `School` row) regardless of test outcome.
"""
from __future__ import annotations

import threading

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from school_timetable.application.errors import (
    ConfigurationChangedDuringGenerationError,
    ConfigurationLockedError,
    DuplicateTeachingAssignmentError,
    TeachingAssignmentNotFoundError,
)
from school_timetable.application.teaching_assignment_rules import validate_create, validate_delete, validate_update
from school_timetable.domain.result import SolverStatus
from school_timetable.fixtures.valid_fixture import build_valid_fixture
from school_timetable.persistence import models as m
from school_timetable.persistence.problem_repository import SessionFactorySchedulingProblemRepository
from school_timetable.persistence.schedule_repository import SqlAlchemyScheduleVersionRepository
from school_timetable.persistence.teaching_assignment_repository import SqlAlchemyTeachingAssignmentRepository
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


def _requirement_ids(session_factory, school_id, year_id):
    problem = SessionFactorySchedulingProblemRepository(session_factory).load_by_school_and_year(school_id, year_id)
    return {r.id for r in problem.teaching_requirements}


# -- A. write persistence -----------------------------------------------

def test_create_round_trip(seeded_db):
    problem, session_factory = seeded_db
    repo = SqlAlchemyTeachingAssignmentRepository(session_factory)

    def validate(current_problem):
        validate_create(
            current_problem, problem.school.id, problem.academic_year.id,
            teacher_id="t_math", participant_group_id="pg_9b", activity_id="art", weekly_periods=3,
        )

    repo.create(
        problem.school.id, problem.academic_year.id, "req_test_create",
        "t_math", "pg_9b", "art", 3, validate=validate,
    )

    reloaded = SessionFactorySchedulingProblemRepository(session_factory).load_by_school_and_year(
        problem.school.id, problem.academic_year.id,
    )
    created = next(r for r in reloaded.teaching_requirements if r.id == "req_test_create")
    assert created.teacher_id == "t_math"
    assert created.participant_group_id == "pg_9b"
    assert created.activity_id == "art"
    assert created.weekly_periods == 3
    assert created.split_group_id is None
    assert created.resource_requirement is None
    assert created.time_preferences == ()


def test_update_round_trip(seeded_db):
    problem, session_factory = seeded_db
    repo = SqlAlchemyTeachingAssignmentRepository(session_factory)

    def validate(current_problem):
        validate_update(
            current_problem, problem.school.id, problem.academic_year.id, "science_8a",
            teacher_id="t_science", participant_group_id="pg_8a", activity_id="science", weekly_periods=7,
        )

    repo.update(
        problem.school.id, problem.academic_year.id, "science_8a",
        "t_science", "pg_8a", "science", 7, validate=validate,
    )

    reloaded = SessionFactorySchedulingProblemRepository(session_factory).load_by_school_and_year(
        problem.school.id, problem.academic_year.id,
    )
    updated = next(r for r in reloaded.teaching_requirements if r.id == "science_8a")
    assert updated.weekly_periods == 7
    assert updated.id == "science_8a"  # natural_id preserved


def test_delete_round_trip(seeded_db):
    problem, session_factory = seeded_db
    repo = SqlAlchemyTeachingAssignmentRepository(session_factory)

    def validate(current_problem):
        validate_delete(current_problem, problem.school.id, problem.academic_year.id, "science_8a")

    repo.delete(problem.school.id, problem.academic_year.id, "science_8a", validate=validate)

    remaining_ids = _requirement_ids(session_factory, problem.school.id, problem.academic_year.id)
    assert "science_8a" not in remaining_ids


def test_same_year_isolation(seeded_db, live_db_engine):
    """A second School/AcademicYear with an overlapping teacher natural
    ID must never be affected by a write scoped to the first year."""
    problem, session_factory = seeded_db

    connection = live_db_engine.connect()
    session = Session(bind=connection)
    school2 = m.School(natural_id="other-school", name="Other School")
    session.add(school2)
    session.flush()
    year2 = m.AcademicYear(school_id=school2.id, natural_id="other-year", label="Other Year")
    session.add(year2)
    session.flush()
    session.add(m.Teacher(academic_year_id=year2.id, natural_id="t_math", name="Other Teacher Math", ordinal=0))
    session.commit()
    year2_id = year2.id
    school2_id = school2.id
    session.close()
    connection.close()

    try:
        repo = SqlAlchemyTeachingAssignmentRepository(session_factory)

        def validate(current_problem):
            validate_create(
                current_problem, problem.school.id, problem.academic_year.id,
                teacher_id="t_math", participant_group_id="pg_9b", activity_id="art", weekly_periods=3,
            )

        repo.create(
            problem.school.id, problem.academic_year.id, "req_isolation_test",
            "t_math", "pg_9b", "art", 3, validate=validate,
        )

        # The new requirement must belong to the FIRST year only.
        first_year_ids = _requirement_ids(session_factory, problem.school.id, problem.academic_year.id)
        assert "req_isolation_test" in first_year_ids

        check_connection = live_db_engine.connect()
        check_session = Session(bind=check_connection)
        second_year_requirements = check_session.execute(
            select(m.TeachingRequirement).where(m.TeachingRequirement.academic_year_id == year2_id)
        ).scalars().all()
        check_session.close()
        check_connection.close()
        assert second_year_requirements == []  # nothing was ever written into the second year
    finally:
        cleanup_connection = live_db_engine.connect()
        cleanup_session = Session(bind=cleanup_connection)
        row = cleanup_session.get(m.School, school2_id)
        if row is not None:
            cleanup_session.delete(row)
            cleanup_session.commit()
        cleanup_session.close()
        cleanup_connection.close()


def test_rollback_leaves_no_partial_mutation_on_validation_failure(seeded_db):
    problem, session_factory = seeded_db
    repo = SqlAlchemyTeachingAssignmentRepository(session_factory)
    before_ids = _requirement_ids(session_factory, problem.school.id, problem.academic_year.id)

    def failing_validate(current_problem):
        raise TeachingAssignmentNotFoundError(problem.school.id, problem.academic_year.id, "does-not-exist")

    with pytest.raises(TeachingAssignmentNotFoundError):
        repo.create(
            problem.school.id, problem.academic_year.id, "req_should_not_exist",
            "t_math", "pg_9b", "art", 3, validate=failing_validate,
        )

    after_ids = _requirement_ids(session_factory, problem.school.id, problem.academic_year.id)
    assert after_ids == before_ids
    assert "req_should_not_exist" not in after_ids


# -- B. duplicate concurrency (genuine, thread-based, lock-serialized) --

def test_concurrent_duplicate_create_exactly_one_succeeds(seeded_db):
    """Two threads race to create the identical (teacher, group,
    activity) triple. Real Postgres row-level locking (Owner Decision
    #36's `SELECT ... FOR UPDATE` on the AcademicYear), not sleep-based
    timing, is what makes this deterministic: whichever thread's lock
    acquisition wins proceeds to reload+validate+insert+commit; the
    other blocks on the lock until the first commits, then reloads
    (now sees the first's committed row) and its own validate()
    correctly rejects it as a duplicate."""
    problem, session_factory = seeded_db
    repo = SqlAlchemyTeachingAssignmentRepository(session_factory)
    barrier = threading.Barrier(2)
    results: dict[str, str] = {}

    def attempt(label: str, natural_id: str) -> None:
        def validate(current_problem):
            validate_create(
                current_problem, problem.school.id, problem.academic_year.id,
                teacher_id="t_math", participant_group_id="pg_9b", activity_id="art", weekly_periods=3,
            )

        barrier.wait(timeout=10)
        try:
            repo.create(
                problem.school.id, problem.academic_year.id, natural_id,
                "t_math", "pg_9b", "art", 3, validate=validate,
            )
            results[label] = "success"
        except DuplicateTeachingAssignmentError:
            results[label] = "duplicate"
        except Exception as exc:  # pragma: no cover - diagnostic only
            results[label] = f"error:{exc!r}"

    t1 = threading.Thread(target=attempt, args=("A", "req_concurrent_a"))
    t2 = threading.Thread(target=attempt, args=("B", "req_concurrent_b"))
    t1.start()
    t2.start()
    t1.join(timeout=15)
    t2.join(timeout=15)

    assert sorted(results.values()) == ["duplicate", "success"], results

    reloaded = SessionFactorySchedulingProblemRepository(session_factory).load_by_school_and_year(
        problem.school.id, problem.academic_year.id,
    )
    matching = [
        r for r in reloaded.teaching_requirements
        if r.teacher_id == "t_math" and r.participant_group_id == "pg_9b" and r.activity_id == "art"
    ]
    assert len(matching) == 1, "the DB must end with exactly one row, never two"


# -- C. generation-vs-write race (Owner Decision #36) --------------------

def test_generation_persist_aborts_when_config_changed_since_load(seeded_db, live_db_engine):
    """Sequential, fully deterministic simulation of: (1) generation
    loads problem P; (2) a config write commits; (3) generation
    attempts to persist against the now-stale P. The mechanism under
    test (frozen-dataclass structural equality between the reloaded-
    under-lock current problem and the caller-supplied `problem`) does
    not depend on thread-interleaving specifics -- encoding the required
    ordering directly, rather than via real thread timing, is strictly
    stronger proof of the comparison logic itself, and the duplicate-
    concurrency test above already proves the lock genuinely serializes
    real concurrent transactions."""
    problem, session_factory = seeded_db
    schedule_repo = SqlAlchemyScheduleVersionRepository(session_factory)
    assignment_repo = SqlAlchemyTeachingAssignmentRepository(session_factory)

    stale_problem = SessionFactorySchedulingProblemRepository(session_factory).load_by_school_and_year(
        problem.school.id, problem.academic_year.id,
    )

    def validate(current_problem):
        validate_create(
            current_problem, problem.school.id, problem.academic_year.id,
            teacher_id="t_math", participant_group_id="pg_9b", activity_id="art", weekly_periods=3,
        )

    assignment_repo.create(
        problem.school.id, problem.academic_year.id, "req_committed_during_generation",
        "t_math", "pg_9b", "art", 3, validate=validate,
    )

    with pytest.raises(ConfigurationChangedDuringGenerationError):
        schedule_repo.persist_initial_version(
            problem.school.id, problem.academic_year.id, stale_problem,
            entries=(), solver_status=SolverStatus.OPTIMAL, total_soft_penalty=0,
            wall_time_seconds=0.01, random_seed=None,
        )

    # No Schedule/ScheduleVersion/ScheduleEntry row was ever created.
    connection = live_db_engine.connect()
    session = Session(bind=connection)
    year_id = session.execute(
        select(m.AcademicYear.id).join(m.School, m.School.id == m.AcademicYear.school_id).where(
            m.School.natural_id == problem.school.id, m.AcademicYear.natural_id == problem.academic_year.id,
        )
    ).scalar_one()
    schedule_count = len(
        session.execute(select(m.Schedule).where(m.Schedule.academic_year_id == year_id)).scalars().all()
    )
    session.close()
    connection.close()
    assert schedule_count == 0


def test_generation_persist_succeeds_then_blocks_a_waiting_config_write(seeded_db):
    """Opposite ordering: generation reaches the final locked persist
    first and commits; a config write attempted afterward must see the
    now-generated schedule and be rejected under Decision #35, with the
    configuration left unchanged."""
    problem, session_factory = seeded_db
    schedule_repo = SqlAlchemyScheduleVersionRepository(session_factory)
    assignment_repo = SqlAlchemyTeachingAssignmentRepository(session_factory)

    loaded_problem = SessionFactorySchedulingProblemRepository(session_factory).load_by_school_and_year(
        problem.school.id, problem.academic_year.id,
    )

    schedule_repo.persist_initial_version(
        problem.school.id, problem.academic_year.id, loaded_problem,
        entries=(), solver_status=SolverStatus.OPTIMAL, total_soft_penalty=0,
        wall_time_seconds=0.01, random_seed=None,
    )

    def validate(current_problem):
        validate_create(
            current_problem, problem.school.id, problem.academic_year.id,
            teacher_id="t_math", participant_group_id="pg_9b", activity_id="art", weekly_periods=3,
        )

    with pytest.raises(ConfigurationLockedError):
        assignment_repo.create(
            problem.school.id, problem.academic_year.id, "req_after_generation",
            "t_math", "pg_9b", "art", 3, validate=validate,
        )

    after_ids = _requirement_ids(session_factory, problem.school.id, problem.academic_year.id)
    assert "req_after_generation" not in after_ids
