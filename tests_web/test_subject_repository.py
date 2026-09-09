"""Real-PostgreSQL integration + concurrency tests for Real-School
Setup MVP Slice D's Subject write path:
`persistence.activity_repository.SqlAlchemyActivityRepository` and the
Owner-Decision-#36 generation-vs-write race closure, now proven for a
Subject (`Activity`) write too (already proven for Teacher/Class/
Teaching Assignment writes in the sibling `test_*_repository.py` files).

Everything here is seeded with a real, committed transaction (like
`test_class_section_repository.py`, for the same reason: some tests
need two independent, overlapping database transactions), and the
`seeded_db` fixture manually deletes every row it created afterward
(cascading from the one `School` row) regardless of test outcome.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from school_timetable.application.errors import (
    ConfigurationChangedDuringGenerationError,
    ConfigurationLockedError,
    SubjectInUseError,
    SubjectNotFoundError,
)
from school_timetable.application.subject_rules import validate_create, validate_delete, validate_update
from school_timetable.domain.result import SolverStatus
from school_timetable.fixtures.valid_fixture import build_valid_fixture
from school_timetable.persistence import models as m
from school_timetable.persistence.activity_repository import SqlAlchemyActivityRepository
from school_timetable.persistence.problem_repository import SessionFactorySchedulingProblemRepository
from school_timetable.persistence.schedule_repository import SqlAlchemyScheduleVersionRepository
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


def _subject_ids(session_factory, school_id, year_id):
    problem = SessionFactorySchedulingProblemRepository(session_factory).load_by_school_and_year(school_id, year_id)
    return {a.id for a in problem.activities if a.kind.value == "ORDINARY"}


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


# -- A. write persistence -----------------------------------------------

def test_create_round_trip(seeded_db):
    problem, session_factory = seeded_db
    repo = SqlAlchemyActivityRepository(session_factory)

    def validate(current_problem):
        validate_create(current_problem, problem.school.id, problem.academic_year.id, name="Physics")

    repo.create(problem.school.id, problem.academic_year.id, "activity_test_create", "Physics", validate=validate)

    reloaded = SessionFactorySchedulingProblemRepository(session_factory).load_by_school_and_year(
        problem.school.id, problem.academic_year.id,
    )
    created = next(a for a in reloaded.activities if a.id == "activity_test_create")
    assert created.name == "Physics"
    assert created.kind.value == "ORDINARY"


def test_create_next_ordinal_is_max_across_all_activities_plus_one(seeded_db, live_db_engine):
    problem, session_factory = seeded_db
    repo = SqlAlchemyActivityRepository(session_factory)

    def validate(current_problem):
        validate_create(current_problem, problem.school.id, problem.academic_year.id, name="Physics")

    repo.create(problem.school.id, problem.academic_year.id, "activity_test_ordinal", "Physics", validate=validate)

    connection = live_db_engine.connect()
    session = Session(bind=connection)
    year_id = session.execute(
        select(m.AcademicYear.id).join(m.School, m.School.id == m.AcademicYear.school_id).where(
            m.School.natural_id == problem.school.id, m.AcademicYear.natural_id == problem.academic_year.id,
        )
    ).scalar_one()
    ordinals = session.execute(
        select(m.Activity.ordinal).where(m.Activity.academic_year_id == year_id).order_by(m.Activity.ordinal)
    ).scalars().all()
    session.close()
    connection.close()
    # Ordinal is computed across ALL activities (ORDINARY + CLUB) --
    # the fixture has 8 ORDINARY + 2 CLUB = 10, so the new row is 10.
    assert ordinals == list(range(len(problem.activities) + 1))


def test_create_validation_failure_leaves_no_partial_mutation(seeded_db):
    """Atomicity proof A: create validation failure -- no Activity
    inserted."""
    problem, session_factory = seeded_db
    repo = SqlAlchemyActivityRepository(session_factory)
    before_ids = _subject_ids(session_factory, problem.school.id, problem.academic_year.id)

    def failing_validate(current_problem):
        raise SubjectNotFoundError(problem.school.id, problem.academic_year.id, "irrelevant")

    with pytest.raises(SubjectNotFoundError):
        repo.create(
            problem.school.id, problem.academic_year.id, "activity_should_not_exist",
            "Physics", validate=failing_validate,
        )

    after_ids = _subject_ids(session_factory, problem.school.id, problem.academic_year.id)
    assert after_ids == before_ids
    assert "activity_should_not_exist" not in after_ids


def test_update_round_trip(seeded_db):
    problem, session_factory = seeded_db
    repo = SqlAlchemyActivityRepository(session_factory)

    def validate(current_problem):
        validate_update(current_problem, problem.school.id, problem.academic_year.id, "math", name="Advanced Math")

    repo.update(problem.school.id, problem.academic_year.id, "math", "Advanced Math", validate=validate)

    reloaded = SessionFactorySchedulingProblemRepository(session_factory).load_by_school_and_year(
        problem.school.id, problem.academic_year.id,
    )
    updated = next(a for a in reloaded.activities if a.id == "math")
    assert updated.name == "Advanced Math"
    assert updated.kind.value == "ORDINARY"


def test_update_preserves_ordinal_and_ids(seeded_db, live_db_engine):
    problem, session_factory = seeded_db
    repo = SqlAlchemyActivityRepository(session_factory)

    connection = live_db_engine.connect()
    session = Session(bind=connection)
    year_id = session.execute(
        select(m.AcademicYear.id).join(m.School, m.School.id == m.AcademicYear.school_id).where(
            m.School.natural_id == problem.school.id, m.AcademicYear.natural_id == problem.academic_year.id,
        )
    ).scalar_one()
    before = session.execute(
        select(m.Activity).where(m.Activity.academic_year_id == year_id, m.Activity.natural_id == "math")
    ).scalar_one()
    id_before, ordinal_before = before.id, before.ordinal
    session.close()
    connection.close()

    def validate(current_problem):
        validate_update(current_problem, problem.school.id, problem.academic_year.id, "math", name="Renamed")

    repo.update(problem.school.id, problem.academic_year.id, "math", "Renamed", validate=validate)

    connection2 = live_db_engine.connect()
    session2 = Session(bind=connection2)
    after = session2.execute(
        select(m.Activity).where(m.Activity.academic_year_id == year_id, m.Activity.natural_id == "math")
    ).scalar_one()
    session2.close()
    connection2.close()

    assert after.id == id_before
    assert after.ordinal == ordinal_before
    assert after.kind == "ORDINARY"


def test_update_validation_failure_leaves_name_unchanged(seeded_db):
    """Atomicity proof B: update validation failure -- name unchanged."""
    problem, session_factory = seeded_db
    repo = SqlAlchemyActivityRepository(session_factory)

    def failing_validate(current_problem):
        raise SubjectNotFoundError(problem.school.id, problem.academic_year.id, "math")

    with pytest.raises(SubjectNotFoundError):
        repo.update(problem.school.id, problem.academic_year.id, "math", "Renamed", validate=failing_validate)

    reloaded = SessionFactorySchedulingProblemRepository(session_factory).load_by_school_and_year(
        problem.school.id, problem.academic_year.id,
    )
    unchanged = next(a for a in reloaded.activities if a.id == "math")
    assert unchanged.name == "Mathematics"


def test_update_club_row_via_repository_raises_subject_not_found(seeded_db):
    """Even if a caller's `validate` callback were somehow bypassed
    (defense-in-depth), the repository itself authoritatively re-checks
    `row.kind == 'ORDINARY'` against the resolved ORM row."""
    problem, session_factory = seeded_db
    repo = SqlAlchemyActivityRepository(session_factory)

    def permissive_validate(current_problem):
        pass  # Deliberately does not check kind, to prove the repository's own guard.

    with pytest.raises(SubjectNotFoundError):
        repo.update(problem.school.id, problem.academic_year.id, "club_chess", "Renamed", validate=permissive_validate)

    reloaded = SessionFactorySchedulingProblemRepository(session_factory).load_by_school_and_year(
        problem.school.id, problem.academic_year.id,
    )
    club = next(a for a in reloaded.activities if a.id == "club_chess")
    assert club.name == "Chess Club"
    assert club.kind.value == "CLUB"


def test_delete_round_trip(seeded_db):
    problem, session_factory = seeded_db
    repo = SqlAlchemyActivityRepository(session_factory)

    def validate_c(current_problem):
        validate_create(current_problem, problem.school.id, problem.academic_year.id, name="Unused")

    repo.create(problem.school.id, problem.academic_year.id, "activity_to_delete", "Unused", validate=validate_c)

    def validate_d(current_problem):
        validate_delete(current_problem, problem.school.id, problem.academic_year.id, "activity_to_delete")

    repo.delete(problem.school.id, problem.academic_year.id, "activity_to_delete", validate=validate_d)

    remaining_ids = _subject_ids(session_factory, problem.school.id, problem.academic_year.id)
    assert "activity_to_delete" not in remaining_ids


def test_delete_blocker_leaves_nothing_deleted(seeded_db):
    """Atomicity proof C: delete blocked -- Activity remains, reference
    remains."""
    problem, session_factory = seeded_db
    repo = SqlAlchemyActivityRepository(session_factory)

    def validate(current_problem):
        validate_delete(current_problem, problem.school.id, problem.academic_year.id, "math")

    with pytest.raises(SubjectInUseError):
        repo.delete(problem.school.id, problem.academic_year.id, "math", validate=validate)

    remaining_ids = _subject_ids(session_factory, problem.school.id, problem.academic_year.id)
    assert "math" in remaining_ids


def test_delete_club_row_via_repository_raises_subject_not_found(seeded_db):
    """Atomicity proof D: a CLUB target -- CLUB row unchanged."""
    problem, session_factory = seeded_db
    repo = SqlAlchemyActivityRepository(session_factory)

    def permissive_validate(current_problem):
        pass

    with pytest.raises(SubjectNotFoundError):
        repo.delete(problem.school.id, problem.academic_year.id, "club_chess", validate=permissive_validate)

    reloaded = SessionFactorySchedulingProblemRepository(session_factory).load_by_school_and_year(
        problem.school.id, problem.academic_year.id,
    )
    club = next(a for a in reloaded.activities if a.id == "club_chess")
    assert club.name == "Chess Club"


# -- B. generation-vs-write race (Owner Decision #36) --------------------

def test_generation_persist_aborts_when_subject_write_committed_since_load(seeded_db, live_db_engine):
    """Sequential, fully deterministic simulation of: (1) generation
    loads problem P; (2) a Subject write commits; (3) generation
    attempts to persist against the now-stale P. Mirrors
    `test_class_section_repository.py`'s identical proof, now for a
    Subject mutation -- `SchedulingProblem.activities` is itself part
    of the frozen-dataclass equality the generation persist step
    compares."""
    problem, session_factory = seeded_db
    schedule_repo = SqlAlchemyScheduleVersionRepository(session_factory)
    activity_repo = SqlAlchemyActivityRepository(session_factory)

    stale_problem = SessionFactorySchedulingProblemRepository(session_factory).load_by_school_and_year(
        problem.school.id, problem.academic_year.id,
    )

    def validate(current_problem):
        validate_create(current_problem, problem.school.id, problem.academic_year.id, name="Physics")

    activity_repo.create(
        problem.school.id, problem.academic_year.id, "activity_committed_during_generation",
        "Physics", validate=validate,
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


def test_generation_persist_succeeds_then_blocks_a_waiting_subject_write(seeded_db):
    """Opposite ordering: generation reaches the final locked persist
    first and commits; a Subject write attempted afterward must see the
    now-generated schedule and be rejected under Decision #35, with the
    configuration left unchanged."""
    problem, session_factory = seeded_db
    schedule_repo = SqlAlchemyScheduleVersionRepository(session_factory)
    activity_repo = SqlAlchemyActivityRepository(session_factory)

    loaded_problem = SessionFactorySchedulingProblemRepository(session_factory).load_by_school_and_year(
        problem.school.id, problem.academic_year.id,
    )

    schedule_repo.persist_initial_version(
        problem.school.id, problem.academic_year.id, loaded_problem,
        entries=(), solver_status=SolverStatus.OPTIMAL, total_soft_penalty=0,
        wall_time_seconds=0.01, random_seed=None,
    )

    def validate(current_problem):
        validate_create(current_problem, problem.school.id, problem.academic_year.id, name="Physics")

    with pytest.raises(ConfigurationLockedError):
        activity_repo.create(
            problem.school.id, problem.academic_year.id, "activity_after_generation",
            "Physics", validate=validate,
        )

    after_ids = _subject_ids(session_factory, problem.school.id, problem.academic_year.id)
    assert "activity_after_generation" not in after_ids
