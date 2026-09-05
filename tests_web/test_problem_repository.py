"""Real-PostgreSQL integration tests for the Phase 3A2.3
`SqlAlchemySchedulingProblemRepository` -- the primary proof that a
persisted scheduling configuration round-trips into a `SchedulingProblem`
deeply equal to the original (exact tuple order included), and that the
DB-loaded problem passes preflight, solves, and verifies just like the
in-memory one.

Uses the same rollback-based `db_session` fixture pattern already
established in `test_persistence_schema.py`: everything runs inside one
transaction that is rolled back at teardown, so no test leaves rows
behind and the development database is never touched (only
`TEST_DATABASE_URL`, via `live_db_engine`).
"""
from __future__ import annotations

import dataclasses

import pytest
from sqlalchemy.orm import Session

from school_timetable.application.errors import SchedulingProblemNotFoundError
from school_timetable.fixtures.valid_fixture import build_valid_fixture
from school_timetable.persistence.problem_repository import SqlAlchemySchedulingProblemRepository
from school_timetable.scheduling.solver import solve
from school_timetable.validation.preflight import run_preflight
from school_timetable.verification.verifier import verify
from tests_web.support.problem_writer import write_scheduling_problem


@pytest.fixture
def db_session(live_db_engine):
    connection = live_db_engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection)
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


def test_round_trip_deep_equality_with_exact_ordering(db_session):
    problem = build_valid_fixture()

    # Confirm up front that this fixture genuinely exercises ordering --
    # every one of these has more than one element, so a writer/mapper
    # bug that silently reorders (or sorts) would be caught, not masked
    # by a degenerate single-element case.
    merged_group = next(g for g in problem.participant_groups if len(g.class_sections) > 1)
    assert merged_group.class_sections == ("9a", "9b")
    history_req = next(r for r in problem.teaching_requirements if r.id == "history_8a")
    assert len(history_req.time_preferences) >= 1
    chess_block = next(b for b in problem.reserved_blocks if b.id == "club_chess")
    assert len(chess_block.class_sections) == 2
    assert len(problem.days) > 1 and len(problem.periods) > 1

    write_scheduling_problem(db_session, problem)
    db_session.flush()

    repo = SqlAlchemySchedulingProblemRepository(db_session)
    loaded = repo.load_by_school_and_year(problem.school.id, problem.academic_year.id)

    # Deep frozen-dataclass equality -- not a partial/field-by-field
    # comparison. This is the primary Phase 3A2.3 acceptance criterion.
    assert loaded == problem


def test_db_loaded_problem_preflight_solve_verify(db_session):
    problem = build_valid_fixture()
    write_scheduling_problem(db_session, problem)
    db_session.flush()

    repo = SqlAlchemySchedulingProblemRepository(db_session)
    loaded = repo.load_by_school_and_year(problem.school.id, problem.academic_year.id)

    errors = run_preflight(loaded)
    assert errors == []

    original_result = solve(problem)
    loaded_result = solve(loaded)

    assert original_result.is_success
    assert loaded_result.is_success
    assert loaded_result.status == original_result.status

    original_report = verify(problem, original_result.entries)
    loaded_report = verify(loaded, loaded_result.entries)
    assert original_report.passed, original_report.violations
    assert loaded_report.passed, loaded_report.violations


def test_unknown_school_or_year_raises_not_found(db_session):
    problem = build_valid_fixture()
    write_scheduling_problem(db_session, problem)
    db_session.flush()

    repo = SqlAlchemySchedulingProblemRepository(db_session)

    with pytest.raises(SchedulingProblemNotFoundError):
        repo.load_by_school_and_year("no-such-school", problem.academic_year.id)

    with pytest.raises(SchedulingProblemNotFoundError):
        repo.load_by_school_and_year(problem.school.id, "no-such-year")


def test_repository_respects_school_year_scope_with_overlapping_natural_ids(db_session):
    """Two snapshots deliberately share every natural ID *except*
    school/academic_year (a real school could legitimately reuse
    `t_math`, `mon`, etc. across different schools/years) -- the
    repository must load only the requested snapshot, never mixing rows
    in from the other one."""
    base_a, base_b = build_valid_fixture(), build_valid_fixture()
    problem_a = dataclasses.replace(
        base_a,
        school=dataclasses.replace(base_a.school, id="school-a"),
        academic_year=dataclasses.replace(base_a.academic_year, id="year-a"),
    )
    problem_b = dataclasses.replace(
        base_b,
        school=dataclasses.replace(base_b.school, id="school-b"),
        academic_year=dataclasses.replace(base_b.academic_year, id="year-b"),
    )

    write_scheduling_problem(db_session, problem_a)
    write_scheduling_problem(db_session, problem_b)
    db_session.flush()

    repo = SqlAlchemySchedulingProblemRepository(db_session)
    loaded_a = repo.load_by_school_and_year("school-a", "year-a")
    loaded_b = repo.load_by_school_and_year("school-b", "year-b")

    assert loaded_a == problem_a
    assert loaded_b == problem_b
