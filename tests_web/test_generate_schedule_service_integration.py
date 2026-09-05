"""Real-PostgreSQL, real-CP-SAT-solver, real-independent-verifier
end-to-end integration tests for Phase 3A3.3's `GenerateScheduleService`
(`docs/DECISIONS.md` #31). Composes the production objects directly:
`SessionFactorySchedulingProblemRepository`,
`SqlAlchemyScheduleVersionRepository`, `GenerateScheduleService`. No
API/HTTP layer -- that is Phase 3A3.4, not exercised here.

Uses the same `join_transaction_mode="create_savepoint"` fixture pattern
as `tests_web/test_schedule_repository.py`: every adapter-opened
`Session` shares the fixture's single outer, never-committed
`Connection`, so nothing here ever leaves a row in the real database.

The `_TrackedSessionFactory` below wraps that same session factory to
count adapter-owned `Session`s currently open (created but not yet
`.close()`d). The fixture's own outer `Connection`/`Session` used for
seeding is a separate, already-open object this tracker never touches
or counts -- it is the deliberate SAVEPOINT-isolation baseline this
module accounts for explicitly, not evidence of a leaked adapter
session. `run_preflight`/`solve`/`verify` are monkeypatched with thin
wrappers that assert `open_count == 0` immediately before and after
calling through to the *real* function -- proving no repository-owned
Session is open during that window -- while still exercising the real
CP-SAT solver and the real independent verifier, never a fake.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from school_timetable.application import generate_schedule_service as svc_mod
from school_timetable.application.errors import (
    InvalidSchedulingConfigurationError,
    ScheduleAlreadyExistsError,
    ScheduleInfeasibleError,
)
from school_timetable.application.generate_schedule_service import (
    GenerateScheduleService,
    ScheduleVerificationFailedError,
)
from school_timetable.domain.result import SolverStatus
from school_timetable.fixtures.impossible_fixture import build_impossible_fixture
from school_timetable.fixtures.school_scale.scenarios import build_school_scale_impossible
from school_timetable.fixtures.valid_fixture import build_valid_fixture
from school_timetable.persistence import models as m
from school_timetable.persistence.problem_repository import SessionFactorySchedulingProblemRepository
from school_timetable.persistence.schedule_repository import SqlAlchemyScheduleVersionRepository
from school_timetable.scheduling.options import SolverOptions
from school_timetable.scheduling.solver import solve as real_solve
from school_timetable.validation.preflight import run_preflight as real_run_preflight
from school_timetable.verification.verifier import VerificationReport
from school_timetable.verification.verifier import verify as real_verify
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


class _TrackedSessionFactory:
    """See module docstring: counts adapter-owned `Session`s currently
    open, so a test can assert none are open during a given window."""

    def __init__(self, session_factory):
        self._session_factory = session_factory
        self.open_count = 0

    def __call__(self):
        session = self._session_factory()
        self.open_count += 1
        original_close = session.close

        def _tracked_close():
            self.open_count -= 1
            original_close()

        session.close = _tracked_close
        return session


def _counts(session: Session, academic_year_id: int) -> dict[str, int]:
    def _count(model):
        return len(
            session.execute(select(model).where(model.academic_year_id == academic_year_id)).scalars().all()
        )

    return {
        "schedule": _count(m.Schedule),
        "schedule_version": _count(m.ScheduleVersion),
        "schedule_entry": _count(m.ScheduleEntry),
        "locked_occurrence": _count(m.LockedOccurrence),
    }


def _year_id(session: Session, academic_year_natural_id: str) -> int:
    return session.execute(
        select(m.AcademicYear.id).where(m.AcademicYear.natural_id == academic_year_natural_id)
    ).scalar_one()


def test_full_generation_round_trip_and_db_free_solve_verify_window(db, monkeypatch):
    session, session_factory = db
    problem = build_valid_fixture()
    write_scheduling_problem(session, problem)
    session.flush()

    tracked_factory = _TrackedSessionFactory(session_factory)
    problem_repo = SessionFactorySchedulingProblemRepository(tracked_factory)
    schedule_repo = SqlAlchemyScheduleVersionRepository(tracked_factory)
    service = GenerateScheduleService(problem_repo, schedule_repo)

    captured: dict = {}

    def preflight_wrapper(p):
        assert tracked_factory.open_count == 0, "adapter session open during preflight"
        result = real_run_preflight(p)
        assert tracked_factory.open_count == 0, "adapter session open during preflight"
        return result

    def solve_wrapper(p, options):
        assert tracked_factory.open_count == 0, "adapter session open during solve"
        result = real_solve(p, options)
        assert tracked_factory.open_count == 0, "adapter session open during solve"
        captured["result"] = result
        return result

    def verify_wrapper(p, entries):
        assert tracked_factory.open_count == 0, "adapter session open during verify"
        report = real_verify(p, entries)
        assert tracked_factory.open_count == 0, "adapter session open during verify"
        captured["report"] = report
        return report

    monkeypatch.setattr(svc_mod, "run_preflight", preflight_wrapper)
    monkeypatch.setattr(svc_mod, "solve", solve_wrapper)
    monkeypatch.setattr(svc_mod, "verify", verify_wrapper)

    returned = service.generate(problem.school.id, problem.academic_year.id)

    # (1)+(2) both repository precheck/load calls finished and closed
    # their sessions before this point (proven implicitly: open_count
    # was already 0 when the first wrapper ran).
    # (3) DB-free preflight/solve/verify window, proven directly above.
    assert captured["result"].status in (SolverStatus.OPTIMAL, SolverStatus.FEASIBLE)
    assert captured["report"].passed, captured["report"].violations

    # (4) persistence only opened a session after verifier success --
    # `returned` is exactly what the (real, tracked) persist call gave
    # back, and the tracked factory's count is back to 0 now that
    # `generate()` has returned.
    assert tracked_factory.open_count == 0

    # Exact round-trip: what the real solver produced is exactly what
    # was persisted, tuple order included (not a set comparison).
    assert returned.version_number == 1
    assert returned.solver_status == captured["result"].status
    assert returned.total_soft_penalty == captured["result"].total_soft_penalty
    assert returned.wall_time_seconds == captured["result"].metadata["wall_time_seconds"]
    assert returned.entries == captured["result"].entries
    assert returned.locked_occurrences == frozenset()

    # Plain, fully detached dataclasses -- no surrogate ID/ordinal leaks.
    assert not hasattr(returned, "_sa_instance_state")
    assert all(not hasattr(e, "_sa_instance_state") for e in returned.entries)
    assert not any(hasattr(e, "ordinal") for e in returned.entries)

    year_id = _year_id(session, problem.academic_year.id)
    counts = _counts(session, year_id)
    assert counts["schedule"] == 1
    assert counts["schedule_version"] == 1
    assert counts["schedule_entry"] == len(captured["result"].entries)
    assert counts["locked_occurrence"] == 0

    reloaded = SqlAlchemyScheduleVersionRepository(session_factory).get_active_schedule(
        problem.school.id, problem.academic_year.id
    )
    assert reloaded == returned

    # A second generate() call for the same year fails at the ordinary
    # existing-schedule precheck -- no second version is ever created.
    with pytest.raises(ScheduleAlreadyExistsError):
        service.generate(problem.school.id, problem.academic_year.id)
    assert _counts(session, year_id)["schedule_version"] == 1


def test_invalid_configuration_leaves_schedule_tables_empty(db):
    session, session_factory = db
    problem = build_impossible_fixture()
    write_scheduling_problem(session, problem)
    session.flush()

    problem_repo = SessionFactorySchedulingProblemRepository(session_factory)
    schedule_repo = SqlAlchemyScheduleVersionRepository(session_factory)
    service = GenerateScheduleService(problem_repo, schedule_repo)

    with pytest.raises(InvalidSchedulingConfigurationError) as exc_info:
        service.generate(problem.school.id, problem.academic_year.id)

    assert exc_info.value.validation_errors
    year_id = _year_id(session, problem.academic_year.id)
    assert _counts(session, year_id) == {
        "schedule": 0, "schedule_version": 0, "schedule_entry": 0, "locked_occurrence": 0,
    }


def test_infeasible_valid_problem_leaves_schedule_tables_empty(db):
    session, session_factory = db
    problem = build_school_scale_impossible()
    write_scheduling_problem(session, problem)
    session.flush()

    problem_repo = SessionFactorySchedulingProblemRepository(session_factory)
    schedule_repo = SqlAlchemyScheduleVersionRepository(session_factory)
    service = GenerateScheduleService(problem_repo, schedule_repo)

    with pytest.raises(ScheduleInfeasibleError):
        service.generate(
            problem.school.id, problem.academic_year.id,
            solver_options=SolverOptions(max_time_seconds=10.0, random_seed=1),
        )

    year_id = _year_id(session, problem.academic_year.id)
    assert _counts(session, year_id) == {
        "schedule": 0, "schedule_version": 0, "schedule_entry": 0, "locked_occurrence": 0,
    }


def test_verifier_failure_leaves_schedule_tables_empty(db, monkeypatch):
    """A forced, monkeypatched verifier rejection (never a mutation of
    production solver/verifier logic) proves the hard persistence gate:
    solver success + verifier failure must never write a row."""
    session, session_factory = db
    problem = build_valid_fixture()
    write_scheduling_problem(session, problem)
    session.flush()

    problem_repo = SessionFactorySchedulingProblemRepository(session_factory)
    schedule_repo = SqlAlchemyScheduleVersionRepository(session_factory)
    service = GenerateScheduleService(problem_repo, schedule_repo)

    monkeypatch.setattr(
        svc_mod, "verify",
        lambda p, entries: VerificationReport(passed=False, violations=("forced failure for this test",)),
    )

    with pytest.raises(ScheduleVerificationFailedError):
        service.generate(problem.school.id, problem.academic_year.id)

    year_id = _year_id(session, problem.academic_year.id)
    assert _counts(session, year_id) == {
        "schedule": 0, "schedule_version": 0, "schedule_entry": 0, "locked_occurrence": 0,
    }
