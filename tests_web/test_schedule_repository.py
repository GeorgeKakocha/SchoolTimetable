"""Real-PostgreSQL integration tests for the Phase 3A3.2 schedule
persistence adapters (`docs/DECISIONS.md` #31):
`persistence.schedule_repository.SqlAlchemyScheduleVersionRepository`
and `persistence.problem_repository.SessionFactorySchedulingProblemRepository`.

Both adapters are session-factory-backed: every public method opens and
closes its own short `Session` rather than being constructed with one.
To prove that internal `session.commit()`/`session.rollback()` calls
behave correctly under this pattern *without* ever touching the real
development database, every `Session` these adapters open here is bound
to the same already-`connection.begin()`-started `Connection` as the
fixture's own seeding `Session`, using SQLAlchemy's documented
"join a Session into an external transaction" pattern
(`join_transaction_mode="create_savepoint"`): each adapter-opened
`Session` gets its own SAVEPOINT, so its own `commit()`/`rollback()`
only affects its own work, never the outer, never-committed transaction
-- which is still rolled back at teardown exactly like every other
`tests_web` fixture, so nothing here ever leaves a row behind.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from school_timetable.application.errors import (
    ScheduleAlreadyExistsError,
    SchedulingProblemNotFoundError,
)
from school_timetable.domain.result import SolverStatus
from school_timetable.fixtures.valid_fixture import build_valid_fixture
from school_timetable.persistence import models as m
from school_timetable.persistence.problem_repository import SessionFactorySchedulingProblemRepository
from school_timetable.persistence.schedule_repository import (
    CorruptScheduleStateError,
    SqlAlchemyScheduleVersionRepository,
)
from school_timetable.scheduling.solver import solve
from tests_web.support.problem_writer import write_scheduling_problem


@pytest.fixture
def db(live_db_engine):
    connection = live_db_engine.connect()
    transaction = connection.begin()
    session_factory = sessionmaker(
        bind=connection,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        join_transaction_mode="create_savepoint",
    )
    session = session_factory()
    try:
        yield session, session_factory
    finally:
        session.close()
        transaction.rollback()
        connection.close()


def _seed(session: Session):
    problem = build_valid_fixture()
    write_scheduling_problem(session, problem)
    session.flush()
    result = solve(problem)
    assert result.is_success
    return problem, result


def _counts(session: Session, academic_year_id: int) -> dict[str, int]:
    def _count(model):
        return len(session.execute(select(model).where(model.academic_year_id == academic_year_id)).scalars().all())

    return {
        "schedule": _count(m.Schedule),
        "schedule_version": _count(m.ScheduleVersion),
        "schedule_entry": _count(m.ScheduleEntry),
        "locked_occurrence": _count(m.LockedOccurrence),
    }


def _tracking_factory(session_factory):
    """Wraps `session_factory` so every `Session` it hands out has its
    `.close()` call recorded -- a concrete proof (not an inference) that
    the adapter under test closes every `Session` it opens."""
    closed = []

    def factory():
        session = session_factory()
        original_close = session.close

        def _tracked_close():
            closed.append(True)
            original_close()

        session.close = _tracked_close
        return session

    return factory, closed


def test_persist_then_read_back_exact_order_and_full_reconstruction(db):
    session, session_factory = db
    problem, result = _seed(session)

    repo = SqlAlchemyScheduleVersionRepository(session_factory)

    assert repo.get_active_schedule(problem.school.id, problem.academic_year.id) is None

    persisted = repo.persist_initial_version(
        problem.school.id,
        problem.academic_year.id,
        result.entries,
        result.status,
        result.total_soft_penalty,
        wall_time_seconds=2.5,
        random_seed=None,
    )

    assert persisted.version_number == 1
    assert persisted.solver_status == result.status
    assert persisted.total_soft_penalty == result.total_soft_penalty
    assert persisted.wall_time_seconds == 2.5
    assert persisted.random_seed is None
    assert persisted.locked_occurrences == frozenset()
    # Tuple equality is order-sensitive -- this is the round-trip proof,
    # not a set/membership comparison. Guard against a mapper bug that
    # could silently reorder while still returning *a* tuple: with 160+
    # entries, a reversed tuple is provably not equal to the original.
    assert persisted.entries == result.entries
    assert tuple(reversed(persisted.entries)) != result.entries

    # Returned objects are plain, fully detached dataclasses -- never an
    # ORM row -- so nothing here depends on any Session, open or closed.
    assert not hasattr(persisted, "_sa_instance_state")
    assert all(not hasattr(e, "_sa_instance_state") for e in persisted.entries)

    # Read back via a second, independent repository instance -- proves
    # this is a genuine DB-sourced read, not a value cached on `repo` or
    # `persisted`.
    reloaded = SqlAlchemyScheduleVersionRepository(session_factory).get_active_schedule(
        problem.school.id, problem.academic_year.id
    )
    assert reloaded == persisted
    assert not hasattr(reloaded, "_sa_instance_state")
    assert all(not hasattr(e, "_sa_instance_state") for e in reloaded.entries)


def test_get_active_schedule_none_when_school_year_exists_but_no_schedule(db):
    """Case A: a valid school/year with no `Schedule` row at all --
    `get_active_schedule` returns `None`."""
    session, session_factory = db
    problem, _ = _seed(session)

    repo = SqlAlchemyScheduleVersionRepository(session_factory)
    assert repo.get_active_schedule(problem.school.id, problem.academic_year.id) is None


def test_get_active_schedule_raises_on_corrupt_schedule_with_null_active_version(db):
    """Case C: a `Schedule` row that IS persisted but has
    `active_version_id IS NULL` is corrupt state -- distinct from "no
    schedule yet" (case A) -- and must raise a loud internal defect, not
    `None`, not `ScheduleAlreadyExistsError`. Constructed directly via the
    ORM here (test-only), deliberately bypassing
    `persist_initial_version` entirely, so the production repository
    sees exactly this persisted state and nothing else."""
    session, session_factory = db
    problem, _ = _seed(session)
    year_id = session.execute(
        select(m.AcademicYear.id).where(m.AcademicYear.natural_id == problem.academic_year.id)
    ).scalar_one()

    session.add(m.Schedule(academic_year_id=year_id, active_version_id=None))
    session.flush()

    repo = SqlAlchemyScheduleVersionRepository(session_factory)
    with pytest.raises(CorruptScheduleStateError) as exc_info:
        repo.get_active_schedule(problem.school.id, problem.academic_year.id)

    assert not isinstance(exc_info.value, ScheduleAlreadyExistsError)
    message = str(exc_info.value)
    assert problem.school.id in message
    assert problem.academic_year.id in message


def test_get_active_schedule_raises_not_found_for_unknown_school_year(db):
    _session, session_factory = db
    repo = SqlAlchemyScheduleVersionRepository(session_factory)

    with pytest.raises(SchedulingProblemNotFoundError):
        repo.get_active_schedule("no-such-school", "no-such-year")


def test_persist_initial_version_raises_not_found_for_unknown_school_year(db):
    _session, session_factory = db
    repo = SqlAlchemyScheduleVersionRepository(session_factory)

    with pytest.raises(SchedulingProblemNotFoundError):
        repo.persist_initial_version(
            "no-such-school", "no-such-year", (), SolverStatus.OPTIMAL, 0, 0.0, None,
        )


def test_persist_initial_version_rolls_back_completely_on_unrelated_integrity_violation(db):
    """An integrity violation that is NOT `uq_schedule_academic_year_id`
    (here: `ck_schedule_version_solver_status`, via a deliberately
    invalid `solver_status`) must propagate as a real `IntegrityError`,
    never `ScheduleAlreadyExistsError` -- and must leave zero rows in
    every one of the four new tables, proving the write is atomic."""
    session, session_factory = db
    problem, result = _seed(session)
    year_id = session.execute(
        select(m.AcademicYear.id).where(m.AcademicYear.natural_id == problem.academic_year.id)
    ).scalar_one()

    repo = SqlAlchemyScheduleVersionRepository(session_factory)

    with pytest.raises(IntegrityError) as exc_info:
        repo.persist_initial_version(
            problem.school.id,
            problem.academic_year.id,
            result.entries,
            SolverStatus.INFEASIBLE,  # never a valid persisted status
            result.total_soft_penalty,
            wall_time_seconds=1.0,
            random_seed=None,
        )
    assert exc_info.value.orig.diag.constraint_name == "ck_schedule_version_solver_status"

    assert _counts(session, year_id) == {
        "schedule": 0, "schedule_version": 0, "schedule_entry": 0, "locked_occurrence": 0,
    }


def test_persist_initial_version_second_call_raises_schedule_already_exists(db):
    session, session_factory = db
    problem, result = _seed(session)
    year_id = session.execute(
        select(m.AcademicYear.id).where(m.AcademicYear.natural_id == problem.academic_year.id)
    ).scalar_one()

    repo = SqlAlchemyScheduleVersionRepository(session_factory)
    first = repo.persist_initial_version(
        problem.school.id, problem.academic_year.id, result.entries, result.status,
        result.total_soft_penalty, 2.5, None,
    )

    with pytest.raises(ScheduleAlreadyExistsError) as exc_info:
        repo.persist_initial_version(
            problem.school.id, problem.academic_year.id, result.entries, result.status,
            result.total_soft_penalty, 2.5, None,
        )

    err = exc_info.value
    assert err.school_natural_id == problem.school.id
    assert err.academic_year_natural_id == problem.academic_year.id
    # The application-owned conflict error must be a genuinely clean
    # exception -- no SQLAlchemy/psycopg exception reachable through
    # either normal Python exception-chaining attribute, and no extra
    # attributes beyond the two safe natural IDs. The actual violated
    # constraint is proven separately below, never through this error.
    assert err.__cause__ is None
    assert err.__context__ is None
    assert vars(err).keys() == {"school_natural_id", "academic_year_natural_id"}

    # Exactly one Schedule/Version survives the losing attempt's rollback
    # -- the winning attempt's own savepoint was already committed and is
    # untouched by the loser's savepoint rollback -- and the original
    # active version is unchanged.
    counts = _counts(session, year_id)
    assert counts["schedule"] == 1
    assert counts["schedule_version"] == 1
    assert counts["schedule_entry"] == len(result.entries)
    assert counts["locked_occurrence"] == 0

    reloaded = SqlAlchemyScheduleVersionRepository(session_factory).get_active_schedule(
        problem.school.id, problem.academic_year.id
    )
    assert reloaded == first


def test_uq_schedule_academic_year_id_is_the_underlying_conflict_constraint(db):
    """Isolated, persistence-only proof that the real DB constraint
    guarding the canonical-Schedule-per-year invariant is named exactly
    `uq_schedule_academic_year_id` -- kept deliberately separate from the
    application-level conflict test above, which must never (and no
    longer does) expose this diagnostic through
    `ScheduleAlreadyExistsError`."""
    session, session_factory = db
    problem, _ = _seed(session)
    year_id = session.execute(
        select(m.AcademicYear.id).where(m.AcademicYear.natural_id == problem.academic_year.id)
    ).scalar_one()

    session.add(m.Schedule(academic_year_id=year_id, active_version_id=None))
    session.flush()

    with pytest.raises(IntegrityError) as exc_info:
        with session.begin_nested():
            session.add(m.Schedule(academic_year_id=year_id, active_version_id=None))
            session.flush()

    assert exc_info.value.orig.diag.constraint_name == "uq_schedule_academic_year_id"


def test_repository_closes_every_session_it_opens(db):
    session, session_factory = db
    problem, result = _seed(session)

    tracking_factory, closed = _tracking_factory(session_factory)
    repo = SqlAlchemyScheduleVersionRepository(tracking_factory)

    assert repo.get_active_schedule(problem.school.id, problem.academic_year.id) is None
    repo.persist_initial_version(
        problem.school.id, problem.academic_year.id, result.entries, result.status,
        result.total_soft_penalty, 2.5, None,
    )
    repo.get_active_schedule(problem.school.id, problem.academic_year.id)

    assert len(closed) == 3
    assert all(closed)


def test_session_factory_scheduling_problem_repository_round_trip(db):
    session, session_factory = db
    problem, _ = _seed(session)

    repo = SessionFactorySchedulingProblemRepository(session_factory)
    loaded = repo.load_by_school_and_year(problem.school.id, problem.academic_year.id)

    assert loaded == problem
    assert not hasattr(loaded, "_sa_instance_state")
    assert not hasattr(loaded.teaching_requirements[0], "_sa_instance_state")

    # The seeding `session` (a separate Session sharing the same
    # connection) is still perfectly usable afterward -- proof the
    # generation-safe repository's internal Session was closed cleanly,
    # not left holding the connection or an aborted transaction.
    assert session.execute(select(m.School.natural_id)).scalars().all() == [problem.school.id]


def test_session_factory_scheduling_problem_repository_not_found(db):
    _session, session_factory = db
    repo = SessionFactorySchedulingProblemRepository(session_factory)

    with pytest.raises(SchedulingProblemNotFoundError):
        repo.load_by_school_and_year("no-such-school", "no-such-year")
