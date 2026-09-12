"""Real-PostgreSQL integration + concurrency tests for Real-School
Setup MVP Slice B's Teacher write path:
`persistence.teacher_repository.SqlAlchemyTeacherRepository` and the
Owner-Decision-#36 generation-vs-write race closure, now proven for a
Teacher write too (already proven for Teaching Assignment writes in
`test_teaching_assignment_repository.py`).

Everything here is seeded with a real, committed transaction (like
`test_teaching_assignment_repository.py`, for the same reason: some
tests need two independent, overlapping database transactions), and the
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
    TeacherInUseError,
    TeacherNotFoundError,
)
from school_timetable.application.teacher_rules import validate_create, validate_delete, validate_update
from school_timetable.domain.result import SolverStatus
from school_timetable.fixtures.valid_fixture import build_valid_fixture
from school_timetable.persistence import models as m
from school_timetable.persistence.problem_repository import SessionFactorySchedulingProblemRepository
from school_timetable.persistence.schedule_repository import SqlAlchemyScheduleVersionRepository
from school_timetable.persistence.teacher_repository import SqlAlchemyTeacherRepository
from tests_web.support.problem_writer import create_draft_configuration_revision, write_scheduling_problem


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


def _teacher_ids(session_factory, school_id, year_id):
    problem = SessionFactorySchedulingProblemRepository(session_factory).load_by_school_and_year(school_id, year_id)
    return {t.id for t in problem.teachers}


# -- A. write persistence -----------------------------------------------

def test_create_round_trip(seeded_db):
    problem, session_factory = seeded_db
    repo = SqlAlchemyTeacherRepository(session_factory)

    def validate(current_problem):
        validate_create(
            current_problem, problem.school.id, problem.academic_year.id,
            first_name="George", last_name="Kakochashvili",
        )

    repo.create(
        problem.school.id, problem.academic_year.id, "teacher_test_create",
        "George", "Kakochashvili", validate=validate,
    )

    reloaded = SessionFactorySchedulingProblemRepository(session_factory).load_by_school_and_year(
        problem.school.id, problem.academic_year.id,
    )
    created = next(t for t in reloaded.teachers if t.id == "teacher_test_create")
    assert created.first_name == "George"
    assert created.last_name == "Kakochashvili"
    assert created.full_name == "George Kakochashvili"


def test_create_next_ordinal_is_max_plus_one(seeded_db, live_db_engine):
    problem, session_factory = seeded_db
    repo = SqlAlchemyTeacherRepository(session_factory)

    def validate(current_problem):
        validate_create(
            current_problem, problem.school.id, problem.academic_year.id,
            first_name="George", last_name="Kakochashvili",
        )

    repo.create(
        problem.school.id, problem.academic_year.id, "teacher_test_ordinal",
        "George", "Kakochashvili", validate=validate,
    )

    connection = live_db_engine.connect()
    session = Session(bind=connection)
    year_id = session.execute(
        select(m.AcademicYear.id).join(m.School, m.School.id == m.AcademicYear.school_id).where(
            m.School.natural_id == problem.school.id, m.AcademicYear.natural_id == problem.academic_year.id,
        )
    ).scalar_one()
    ordinals = session.execute(
        select(m.Teacher.ordinal).where(m.Teacher.academic_year_id == year_id).order_by(m.Teacher.ordinal)
    ).scalars().all()
    session.close()
    connection.close()
    assert ordinals == list(range(len(problem.teachers) + 1))


def test_update_round_trip(seeded_db):
    problem, session_factory = seeded_db
    repo = SqlAlchemyTeacherRepository(session_factory)

    def validate(current_problem):
        validate_update(
            current_problem, problem.school.id, problem.academic_year.id, "t_math",
            first_name="New", last_name="Name",
        )

    repo.update(problem.school.id, problem.academic_year.id, "t_math", "New", "Name", validate=validate)

    reloaded = SessionFactorySchedulingProblemRepository(session_factory).load_by_school_and_year(
        problem.school.id, problem.academic_year.id,
    )
    updated = next(t for t in reloaded.teachers if t.id == "t_math")
    assert updated.first_name == "New"
    assert updated.last_name == "Name"
    assert updated.id == "t_math"  # natural_id preserved


def test_delete_round_trip(seeded_db):
    problem, session_factory = seeded_db
    repo = SqlAlchemyTeacherRepository(session_factory)

    def validate(current_problem):
        validate_create(
            current_problem, problem.school.id, problem.academic_year.id,
            first_name="Unused", last_name="Teacher",
        )

    repo.create(
        problem.school.id, problem.academic_year.id, "teacher_to_delete",
        "Unused", "Teacher", validate=validate,
    )

    def validate_del(current_problem):
        validate_delete(current_problem, problem.school.id, problem.academic_year.id, "teacher_to_delete")

    repo.delete(problem.school.id, problem.academic_year.id, "teacher_to_delete", validate=validate_del)

    remaining_ids = _teacher_ids(session_factory, problem.school.id, problem.academic_year.id)
    assert "teacher_to_delete" not in remaining_ids


def test_same_year_isolation(seeded_db, live_db_engine):
    """A second School/AcademicYear with an overlapping teacher natural
    ID must never be affected by a write scoped to the first year."""
    problem, session_factory = seeded_db

    connection = live_db_engine.connect()
    session = Session(bind=connection)
    school2 = m.School(natural_id="other-school-teacher-crud", name="Other School")
    session.add(school2)
    session.flush()
    year2 = m.AcademicYear(school_id=school2.id, natural_id="other-year", label="Other Year")
    session.add(year2)
    session.flush()
    revision2_id = create_draft_configuration_revision(session, year2.id)
    session.add(m.Teacher(
        academic_year_id=year2.id, configuration_revision_id=revision2_id, natural_id="teacher_test_create",
        first_name="Other George", last_name="Other Kakochashvili", ordinal=0,
    ))
    session.commit()
    school2_id = school2.id
    year2_id = year2.id
    session.close()
    connection.close()

    try:
        repo = SqlAlchemyTeacherRepository(session_factory)

        def validate(current_problem):
            validate_create(
                current_problem, problem.school.id, problem.academic_year.id,
                first_name="George", last_name="Kakochashvili",
            )

        repo.create(
            problem.school.id, problem.academic_year.id, "teacher_test_create",
            "George", "Kakochashvili", validate=validate,
        )

        first_year_ids = _teacher_ids(session_factory, problem.school.id, problem.academic_year.id)
        assert "teacher_test_create" in first_year_ids

        check_connection = live_db_engine.connect()
        check_session = Session(bind=check_connection)
        second_year_teacher = check_session.execute(
            select(m.Teacher).where(
                m.Teacher.academic_year_id == year2_id, m.Teacher.natural_id == "teacher_test_create",
            )
        ).scalar_one()
        check_session.close()
        check_connection.close()
        # The second year's own pre-existing row is untouched.
        assert second_year_teacher.first_name == "Other George"
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
    repo = SqlAlchemyTeacherRepository(session_factory)
    before_ids = _teacher_ids(session_factory, problem.school.id, problem.academic_year.id)

    def failing_validate(current_problem):
        raise TeacherNotFoundError(problem.school.id, problem.academic_year.id, "does-not-exist")

    with pytest.raises(TeacherNotFoundError):
        repo.create(
            problem.school.id, problem.academic_year.id, "teacher_should_not_exist",
            "George", "Kakochashvili", validate=failing_validate,
        )

    after_ids = _teacher_ids(session_factory, problem.school.id, problem.academic_year.id)
    assert after_ids == before_ids
    assert "teacher_should_not_exist" not in after_ids


# -- B. delete referential safety ----------------------------------------

def test_delete_teaching_requirement_reference_rejected_and_not_deleted(seeded_db):
    problem, session_factory = seeded_db
    repo = SqlAlchemyTeacherRepository(session_factory)

    def validate(current_problem):
        validate_delete(current_problem, problem.school.id, problem.academic_year.id, "t_math")

    with pytest.raises(TeacherInUseError) as exc_info:
        repo.delete(problem.school.id, problem.academic_year.id, "t_math", validate=validate)
    assert exc_info.value.referenced_by == ("TEACHING_REQUIREMENT",)

    remaining_ids = _teacher_ids(session_factory, problem.school.id, problem.academic_year.id)
    assert "t_math" in remaining_ids


# -- C. generation-vs-write race (Owner Decision #36) --------------------

def test_generation_persist_aborts_when_teacher_write_committed_since_load(seeded_db, live_db_engine):
    """Sequential, fully deterministic simulation of: (1) generation
    loads problem P; (2) a Teacher write commits; (3) generation
    attempts to persist against the now-stale P. Mirrors
    `test_teaching_assignment_repository.py`'s identical proof, now for
    a Teacher mutation -- `SchedulingProblem.teachers` is itself part of
    the frozen-dataclass equality the generation persist step compares."""
    problem, session_factory = seeded_db
    schedule_repo = SqlAlchemyScheduleVersionRepository(session_factory)
    teacher_repo = SqlAlchemyTeacherRepository(session_factory)

    stale_problem = SessionFactorySchedulingProblemRepository(session_factory).load_by_school_and_year(
        problem.school.id, problem.academic_year.id,
    )

    def validate(current_problem):
        validate_create(
            current_problem, problem.school.id, problem.academic_year.id,
            first_name="George", last_name="Kakochashvili",
        )

    teacher_repo.create(
        problem.school.id, problem.academic_year.id, "teacher_committed_during_generation",
        "George", "Kakochashvili", validate=validate,
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


def test_generation_persist_succeeds_then_blocks_a_waiting_teacher_write(seeded_db):
    """Opposite ordering: generation reaches the final locked persist
    first and commits; a Teacher write attempted afterward must see the
    now-generated schedule and be rejected under Decision #35, with the
    configuration left unchanged."""
    problem, session_factory = seeded_db
    schedule_repo = SqlAlchemyScheduleVersionRepository(session_factory)
    teacher_repo = SqlAlchemyTeacherRepository(session_factory)

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
            first_name="George", last_name="Kakochashvili",
        )

    with pytest.raises(ConfigurationLockedError):
        teacher_repo.create(
            problem.school.id, problem.academic_year.id, "teacher_after_generation",
            "George", "Kakochashvili", validate=validate,
        )

    after_ids = _teacher_ids(session_factory, problem.school.id, problem.academic_year.id)
    assert "teacher_after_generation" not in after_ids
