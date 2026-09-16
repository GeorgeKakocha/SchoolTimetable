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

import threading
from dataclasses import replace

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from school_timetable.application.errors import (
    ConfigurationChangedDuringGenerationError,
    IncompatibleLocksRequireConfirmationError,
    ScheduleAlreadyExistsError,
    ScheduleVersionNotFoundError,
    SchedulingProblemNotFoundError,
    StaleScheduleVersionError,
)
from school_timetable.application.generate_schedule_service import GenerateScheduleService
from school_timetable.domain.indexing import ProblemIndex
from school_timetable.domain.people import AvailabilityStatus, TeacherAvailability
from school_timetable.domain.result import EntrySource, SolverStatus
from school_timetable.domain.schedule import OccurrenceKey, Schedule
from school_timetable.fixtures.valid_fixture import build_valid_fixture
from school_timetable.persistence import models as m
from school_timetable.persistence.configuration_revision_repository import (
    SqlAlchemyConfigurationRevisionRepository,
)
from school_timetable.persistence.problem_repository import SessionFactorySchedulingProblemRepository
from school_timetable.persistence.schedule_repository import (
    CorruptScheduleStateError,
    SqlAlchemyScheduleVersionRepository,
)
from school_timetable.scheduling.editing import (
    apply_move,
    find_logical_occurrence,
    lock_occurrence,
    unlock_occurrence,
    validate_move,
)
from school_timetable.scheduling.lock_compatibility import classify_locks
from school_timetable.scheduling.options import SolverOptions
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


def _counts_for_version(session: Session, schedule_version_id: int) -> dict[str, int]:
    def _count(model):
        return len(
            session.execute(
                select(model).where(model.schedule_version_id == schedule_version_id)
            ).scalars().all()
        )

    return {"schedule_entry": _count(m.ScheduleEntry), "locked_occurrence": _count(m.LockedOccurrence)}


def _version_row(session: Session, academic_year_id: int, version_number: int) -> m.ScheduleVersion:
    return session.execute(
        select(m.ScheduleVersion).where(
            m.ScheduleVersion.academic_year_id == academic_year_id,
            m.ScheduleVersion.version_number == version_number,
        )
    ).scalar_one()


def _find_move(problem, index, schedule):
    """Finds one valid manual move in `schedule` -- same search strategy
    as `run_editing_demo.py` -- so tests exercise a genuine
    `validate_move`/`apply_move` round trip rather than a hand-built
    candidate `Schedule`. `validate_move`'s own HARD-rule checks
    (including REQUIRED-block-pattern integrity -- see the "manual
    timetable editing correction slice" in `docs/SCHEDULE_EDITING.md`)
    are trusted directly; no defensive post-hoc re-verification is
    needed here."""
    simple_occs, seen = [], set()
    for e in schedule.entries:
        if e.requirement_id is None or (e.requirement_id, e.day_id, e.period_id) in seen:
            continue
        occ_ = find_logical_occurrence(problem, index, schedule, e.requirement_id, e.day_id, e.period_id)
        if occ_.length == 1 and len(occ_.requirement_ids) == 1:
            simple_occs.append((e.requirement_id, e.day_id, e.period_id))
        seen.update((mm.requirement_id, mm.day_id, mm.period_id) for mm in occ_.members)

    for (r1, d1, p1) in simple_occs:
        for (r2, d2, p2) in simple_occs:
            if r1 == r2 or d1 == d2:
                continue
            result = validate_move(problem, schedule, r1, d1, p1, d2, p2, index=index)
            if not result.allowed:
                continue
            swapped = {e.requirement_id for e in result.plan.removed_entries}
            if swapped != {r1, r2}:
                continue
            return result
    raise AssertionError("expected at least one valid move in this fixture")


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
        problem,
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


def test_persist_then_read_back_preserves_reserved_block_resource_id(db):
    """Resources B2 regression: `club_chess`'s `ReservedBlock` is given a
    fixed Resource before solving. `mappers.schedule_entry_to_domain`'s
    RESERVED_BLOCK branch must join back to `ReservedBlock.resource_id`
    exactly like its REQUIREMENT branch already joins back to
    `TeachingRequirement.resource_requirement.resource_id` -- proven by
    the exact same full round-trip equality
    `test_persist_then_read_back_exact_order_and_full_reconstruction`
    already established, narrowed to the one RESERVED_BLOCK entry that
    now carries a Resource. `build_valid_fixture()`'s own two
    ReservedBlocks never carry a Resource, so that broader test alone
    cannot catch a regression here -- this test exists specifically to
    close that gap."""
    session, session_factory = db
    problem = build_valid_fixture()
    problem = replace(
        problem,
        reserved_blocks=tuple(
            replace(b, resource_id="gym") if b.id == "club_chess" else b for b in problem.reserved_blocks
        ),
    )
    write_scheduling_problem(session, problem)
    session.flush()
    result = solve(problem)
    assert result.is_success

    repo = SqlAlchemyScheduleVersionRepository(session_factory)
    persisted = repo.persist_initial_version(
        problem.school.id, problem.academic_year.id, problem, result.entries, result.status,
        result.total_soft_penalty, wall_time_seconds=1.0, random_seed=None,
    )

    persisted_entry = next(e for e in persisted.entries if e.reserved_block_id == "club_chess")
    assert persisted_entry.source == EntrySource.RESERVED_BLOCK
    assert persisted_entry.resource_id == "gym"

    reloaded = SqlAlchemyScheduleVersionRepository(session_factory).get_active_schedule(
        problem.school.id, problem.academic_year.id,
    )
    reloaded_entry = next(e for e in reloaded.entries if e.reserved_block_id == "club_chess")
    assert reloaded_entry.resource_id == "gym"
    assert reloaded == persisted


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
            "no-such-school", "no-such-year", None, (), SolverStatus.OPTIMAL, 0, 0.0, None,
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
            problem,
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
        problem.school.id, problem.academic_year.id, problem, result.entries, result.status,
        result.total_soft_penalty, 2.5, None,
    )

    with pytest.raises(ScheduleAlreadyExistsError) as exc_info:
        repo.persist_initial_version(
            problem.school.id, problem.academic_year.id, problem, result.entries, result.status,
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
        problem.school.id, problem.academic_year.id, problem, result.entries, result.status,
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


# == persist_edited_version (manual-editing backend persistence slice) =======


def _seed_and_persist_v1(session, session_factory):
    problem, result = _seed(session)
    repo = SqlAlchemyScheduleVersionRepository(session_factory)
    v1 = repo.persist_initial_version(
        problem.school.id, problem.academic_year.id, problem, result.entries, result.status,
        result.total_soft_penalty, wall_time_seconds=2.5, random_seed=None,
    )
    return problem, result, repo, v1


def test_persist_edited_version_creates_version_2_and_promotes_it(db):
    session, session_factory = db
    problem, _result, repo, v1 = _seed_and_persist_v1(session, session_factory)
    index = ProblemIndex(problem)
    schedule = Schedule(entries=v1.entries)

    move = _find_move(problem, index, schedule)
    candidate = apply_move(problem, schedule, move)
    assert candidate.entries != v1.entries  # a genuine edit, not a no-op

    v2 = repo.persist_edited_version(
        problem.school.id, problem.academic_year.id, 1, candidate,
        v1.solver_status, v1.total_soft_penalty, wall_time_seconds=0.4, random_seed=None,
    )

    assert v2.version_number == 2
    assert v2.entries == candidate.entries
    assert v2.locked_occurrences == frozenset()

    reloaded = repo.get_active_schedule(problem.school.id, problem.academic_year.id)
    assert reloaded == v2

    year_id = session.execute(
        select(m.AcademicYear.id).where(m.AcademicYear.natural_id == problem.academic_year.id)
    ).scalar_one()
    schedule_row = session.execute(select(m.Schedule).where(m.Schedule.academic_year_id == year_id)).scalar_one()
    v1_row = _version_row(session, year_id, 1)
    v2_row = _version_row(session, year_id, 2)
    assert schedule_row.active_version_id == v2_row.id
    assert v2_row.parent_version_id == v1_row.id


def test_persist_edited_version_preserves_previous_version_entries_unchanged(db):
    """B: the candidate's edited entries land under v2; v1's own
    ScheduleEntry rows are untouched (not rewritten, not deleted)."""
    session, session_factory = db
    problem, result, repo, v1 = _seed_and_persist_v1(session, session_factory)
    index = ProblemIndex(problem)
    schedule = Schedule(entries=v1.entries)
    move = _find_move(problem, index, schedule)
    candidate = apply_move(problem, schedule, move)

    repo.persist_edited_version(
        problem.school.id, problem.academic_year.id, 1, candidate,
        v1.solver_status, v1.total_soft_penalty, 0.4, None,
    )

    year_id = session.execute(
        select(m.AcademicYear.id).where(m.AcademicYear.natural_id == problem.academic_year.id)
    ).scalar_one()
    v1_row = _version_row(session, year_id, 1)
    v1_counts = _counts_for_version(session, v1_row.id)
    assert v1_counts["schedule_entry"] == len(result.entries)
    assert v1_counts["locked_occurrence"] == 0

    v1_entries_still_original = session.execute(
        select(m.ScheduleEntry).where(m.ScheduleEntry.schedule_version_id == v1_row.id)
    ).scalars().all()
    assert len(v1_entries_still_original) == len(result.entries)


def test_persist_edited_version_persists_split_group_lock_and_unlock_is_absence_not_deletion(db):
    """C: locking german_8a locks its split sibling russian_8a too (same
    domain semantics `run_editing_demo.py` exercises); the lock rows land
    under v2 and survive unchanged when v3 unlocks -- unlocking removes
    the lock from the NEW version by simply not including it, it never
    deletes v2's own historical LockedOccurrence rows."""
    session, session_factory = db
    problem, _result, repo, v1 = _seed_and_persist_v1(session, session_factory)
    index = ProblemIndex(problem)
    schedule_v1 = Schedule(entries=v1.entries)

    german_entry = next(e for e in v1.entries if e.requirement_id == "german_8a")
    locked_schedule = lock_occurrence(
        problem, schedule_v1, "german_8a", german_entry.day_id, german_entry.period_id, index=index,
    )
    locked_keys = {k.requirement_id for k in locked_schedule.locked_occurrences}
    assert locked_keys == {"german_8a", "russian_8a"}  # split siblings lock together

    v2 = repo.persist_edited_version(
        problem.school.id, problem.academic_year.id, 1, locked_schedule,
        v1.solver_status, v1.total_soft_penalty, 0.1, None,
    )
    assert {k.requirement_id for k in v2.locked_occurrences} == {"german_8a", "russian_8a"}

    year_id = session.execute(
        select(m.AcademicYear.id).where(m.AcademicYear.natural_id == problem.academic_year.id)
    ).scalar_one()
    v2_row = _version_row(session, year_id, 2)
    assert _counts_for_version(session, v2_row.id)["locked_occurrence"] == 2

    # Now unlock in v3 -- v2's own lock rows must survive untouched.
    unlocked_schedule = unlock_occurrence(
        problem, locked_schedule, "german_8a", german_entry.day_id, german_entry.period_id, index=index,
    )
    assert unlocked_schedule.locked_occurrences == frozenset()

    v3 = repo.persist_edited_version(
        problem.school.id, problem.academic_year.id, 2, unlocked_schedule,
        v1.solver_status, v1.total_soft_penalty, 0.1, None,
    )
    assert v3.locked_occurrences == frozenset()

    assert _counts_for_version(session, v2_row.id)["locked_occurrence"] == 2  # unchanged, historical
    v3_row = _version_row(session, year_id, 3)
    assert _counts_for_version(session, v3_row.id)["locked_occurrence"] == 0


def test_persist_edited_version_rejects_stale_base_with_zero_mutation(db):
    """D: active version is already 2; a caller still holding base
    version 1 must be rejected, with no new row of any kind and the
    active pointer left exactly where it was."""
    session, session_factory = db
    problem, _result, repo, v1 = _seed_and_persist_v1(session, session_factory)
    index = ProblemIndex(problem)
    schedule = Schedule(entries=v1.entries)
    move = _find_move(problem, index, schedule)
    candidate = apply_move(problem, schedule, move)
    v2 = repo.persist_edited_version(
        problem.school.id, problem.academic_year.id, 1, candidate,
        v1.solver_status, v1.total_soft_penalty, 0.4, None,
    )

    year_id = session.execute(
        select(m.AcademicYear.id).where(m.AcademicYear.natural_id == problem.academic_year.id)
    ).scalar_one()
    before = _counts(session, year_id)

    stale_candidate = apply_move(problem, Schedule(entries=v2.entries), _find_move(problem, index, Schedule(entries=v2.entries)))
    with pytest.raises(StaleScheduleVersionError) as exc_info:
        repo.persist_edited_version(
            problem.school.id, problem.academic_year.id, 1, stale_candidate,
            v1.solver_status, v1.total_soft_penalty, 0.4, None,
        )

    err = exc_info.value
    assert err.school_natural_id == problem.school.id
    assert err.academic_year_natural_id == problem.academic_year.id
    assert err.expected_base_version_number == 1
    assert err.actual_active_version_number == 2

    assert _counts(session, year_id) == before
    schedule_row = session.execute(select(m.Schedule).where(m.Schedule.academic_year_id == year_id)).scalar_one()
    v2_row = _version_row(session, year_id, 2)
    assert schedule_row.active_version_id == v2_row.id

    reloaded = repo.get_active_schedule(problem.school.id, problem.academic_year.id)
    assert reloaded == v2


def test_persist_edited_version_rolls_back_completely_on_integrity_violation(db):
    """E: an invalid solver_status trips the same
    `ck_schedule_version_solver_status` constraint `persist_initial_version`'s
    own atomicity test uses -- proving no orphan ScheduleVersion, no
    partial ScheduleEntry/LockedOccurrence rows, and no active_version_id
    change survive a failed persist_edited_version call."""
    session, session_factory = db
    problem, _result, repo, v1 = _seed_and_persist_v1(session, session_factory)
    index = ProblemIndex(problem)
    schedule = Schedule(entries=v1.entries)
    move = _find_move(problem, index, schedule)
    candidate = apply_move(problem, schedule, move)

    year_id = session.execute(
        select(m.AcademicYear.id).where(m.AcademicYear.natural_id == problem.academic_year.id)
    ).scalar_one()
    before = _counts(session, year_id)

    with pytest.raises(IntegrityError) as exc_info:
        repo.persist_edited_version(
            problem.school.id, problem.academic_year.id, 1, candidate,
            SolverStatus.INFEASIBLE,  # never a valid persisted status
            v1.total_soft_penalty, 0.4, None,
        )
    assert exc_info.value.orig.diag.constraint_name == "ck_schedule_version_solver_status"

    assert _counts(session, year_id) == before  # no orphan version_2 row, no partial children
    schedule_row = session.execute(select(m.Schedule).where(m.Schedule.academic_year_id == year_id)).scalar_one()
    v1_row = _version_row(session, year_id, 1)
    assert schedule_row.active_version_id == v1_row.id  # never repointed

    reloaded = repo.get_active_schedule(problem.school.id, problem.academic_year.id)
    assert reloaded == v1


def test_persist_edited_version_sequential_chain_v1_v2_v3(db):
    """F: three linear versions -- parent links correct, version numbers
    monotonic, every historical version intact throughout."""
    session, session_factory = db
    problem, result, repo, v1 = _seed_and_persist_v1(session, session_factory)
    index = ProblemIndex(problem)

    schedule1 = Schedule(entries=v1.entries)
    move1 = _find_move(problem, index, schedule1)
    candidate2 = apply_move(problem, schedule1, move1)
    v2 = repo.persist_edited_version(
        problem.school.id, problem.academic_year.id, 1, candidate2,
        v1.solver_status, v1.total_soft_penalty, 0.4, None,
    )

    schedule2 = Schedule(entries=v2.entries)
    move2 = _find_move(problem, index, schedule2)
    candidate3 = apply_move(problem, schedule2, move2)
    v3 = repo.persist_edited_version(
        problem.school.id, problem.academic_year.id, 2, candidate3,
        v1.solver_status, v1.total_soft_penalty, 0.4, None,
    )

    assert [v1.version_number, v2.version_number, v3.version_number] == [1, 2, 3]

    year_id = session.execute(
        select(m.AcademicYear.id).where(m.AcademicYear.natural_id == problem.academic_year.id)
    ).scalar_one()
    v1_row = _version_row(session, year_id, 1)
    v2_row = _version_row(session, year_id, 2)
    v3_row = _version_row(session, year_id, 3)
    assert v2_row.parent_version_id == v1_row.id
    assert v3_row.parent_version_id == v2_row.id

    # Every historical version's entries remain exactly as originally persisted.
    assert _counts_for_version(session, v1_row.id)["schedule_entry"] == len(result.entries)
    assert _counts_for_version(session, v2_row.id)["schedule_entry"] == len(candidate2.entries)
    assert _counts_for_version(session, v3_row.id)["schedule_entry"] == len(candidate3.entries)

    schedule_row = session.execute(select(m.Schedule).where(m.Schedule.academic_year_id == year_id)).scalar_one()
    assert schedule_row.active_version_id == v3_row.id

    reloaded = repo.get_active_schedule(problem.school.id, problem.academic_year.id)
    assert reloaded == v3


def test_persist_edited_version_raises_not_found_for_unknown_school_year(db):
    _session, session_factory = db
    repo = SqlAlchemyScheduleVersionRepository(session_factory)

    with pytest.raises(SchedulingProblemNotFoundError):
        repo.persist_edited_version(
            "no-such-school", "no-such-year", 1, Schedule(entries=()),
            SolverStatus.OPTIMAL, 0, 0.0, None,
        )


def test_persist_edited_version_raises_corrupt_state_when_no_active_schedule_exists_yet(db):
    """persist_edited_version is never how a first ScheduleVersion is
    created -- calling it before any `persist_initial_version` call is a
    caller-contract violation, not an ordinary 'no schedule yet' outcome
    (unlike `get_active_schedule`, which returns `None` for that case)."""
    session, session_factory = db
    problem, _result = _seed(session)
    repo = SqlAlchemyScheduleVersionRepository(session_factory)

    with pytest.raises(CorruptScheduleStateError):
        repo.persist_edited_version(
            problem.school.id, problem.academic_year.id, 1, Schedule(entries=()),
            SolverStatus.OPTIMAL, 0, 0.0, None,
        )


# == list_versions / get_version (schedule version history + restore slice) ==


def test_list_versions_none_when_no_schedule_exists_yet(db):
    session, session_factory = db
    problem, _ = _seed(session)
    repo = SqlAlchemyScheduleVersionRepository(session_factory)

    assert repo.list_versions(problem.school.id, problem.academic_year.id) is None


def test_list_versions_raises_not_found_for_unknown_school_year(db):
    _session, session_factory = db
    repo = SqlAlchemyScheduleVersionRepository(session_factory)

    with pytest.raises(SchedulingProblemNotFoundError):
        repo.list_versions("no-such-school", "no-such-year")


def test_list_versions_newest_first_with_exactly_one_active_and_correct_parents(db):
    """A: newest-first. B: exactly one is_active. C: parent_version_number
    correct."""
    session, session_factory = db
    problem, _result, repo, v1 = _seed_and_persist_v1(session, session_factory)
    index = ProblemIndex(problem)

    schedule1 = Schedule(entries=v1.entries)
    move1 = _find_move(problem, index, schedule1)
    v2 = repo.persist_edited_version(
        problem.school.id, problem.academic_year.id, 1, apply_move(problem, schedule1, move1),
        v1.solver_status, v1.total_soft_penalty, 0.4, None,
    )
    schedule2 = Schedule(entries=v2.entries)
    move2 = _find_move(problem, index, schedule2)
    v3 = repo.persist_edited_version(
        problem.school.id, problem.academic_year.id, 2, apply_move(problem, schedule2, move2),
        v1.solver_status, v1.total_soft_penalty, 0.4, None,
    )

    history = repo.list_versions(problem.school.id, problem.academic_year.id)

    assert history is not None
    assert [h.version_number for h in history] == [3, 2, 1]  # newest first
    assert sum(1 for h in history if h.is_active) == 1
    active = next(h for h in history if h.is_active)
    assert active.version_number == v3.version_number

    by_number = {h.version_number: h for h in history}
    assert by_number[1].parent_version_number is None
    assert by_number[2].parent_version_number == 1
    assert by_number[3].parent_version_number == 2


def test_get_version_none_when_no_schedule_exists_yet(db):
    session, session_factory = db
    problem, _ = _seed(session)
    repo = SqlAlchemyScheduleVersionRepository(session_factory)

    assert repo.get_version(problem.school.id, problem.academic_year.id, 1) is None


def test_get_version_raises_not_found_for_unknown_school_year(db):
    _session, session_factory = db
    repo = SqlAlchemyScheduleVersionRepository(session_factory)

    with pytest.raises(SchedulingProblemNotFoundError):
        repo.get_version("no-such-school", "no-such-year", 1)


def test_get_version_raises_schedule_version_not_found_for_unknown_version_number(db):
    session, session_factory = db
    problem, _result, repo, _v1 = _seed_and_persist_v1(session, session_factory)

    with pytest.raises(ScheduleVersionNotFoundError) as exc_info:
        repo.get_version(problem.school.id, problem.academic_year.id, 999)

    assert exc_info.value.school_natural_id == problem.school.id
    assert exc_info.value.academic_year_natural_id == problem.academic_year.id
    assert exc_info.value.version_number == 999


def test_get_version_loads_a_specific_non_active_version_in_full(db):
    """D: a specific historical version can be loaded, entries/locks
    included, distinct from whatever is currently active."""
    session, session_factory = db
    problem, _result, repo, v1 = _seed_and_persist_v1(session, session_factory)
    index = ProblemIndex(problem)
    schedule1 = Schedule(entries=v1.entries)
    german_entry = next(e for e in v1.entries if e.requirement_id == "german_8a")
    locked_schedule = lock_occurrence(
        problem, schedule1, "german_8a", german_entry.day_id, german_entry.period_id, index=index,
    )
    v2 = repo.persist_edited_version(
        problem.school.id, problem.academic_year.id, 1, locked_schedule,
        v1.solver_status, v1.total_soft_penalty, 0.1, None,
    )
    move2 = _find_move(problem, index, Schedule(entries=v2.entries, locked_occurrences=v2.locked_occurrences))
    v3 = repo.persist_edited_version(
        problem.school.id, problem.academic_year.id, 2,
        apply_move(problem, Schedule(entries=v2.entries, locked_occurrences=v2.locked_occurrences), move2),
        v1.solver_status, v1.total_soft_penalty, 0.4, None,
    )
    assert v3.version_number == 3  # v3 is now active, not v2

    snapshot_v2 = repo.get_version(problem.school.id, problem.academic_year.id, 2)

    assert snapshot_v2 is not None
    assert snapshot_v2.version_number == 2
    assert snapshot_v2.is_active is False
    assert snapshot_v2.parent_version_number == 1
    assert snapshot_v2.entries == v2.entries
    assert {k.requirement_id for k in snapshot_v2.locked_occurrences} == {"german_8a", "russian_8a"}

    snapshot_v3 = repo.get_version(problem.school.id, problem.academic_year.id, 3)
    assert snapshot_v3 is not None
    assert snapshot_v3.is_active is True
    assert snapshot_v3.parent_version_number == 2


def test_restore_via_persist_edited_version_rolls_back_atomically_on_integrity_violation(db):
    """R: restore's persistence step reuses `persist_edited_version`
    unchanged (no second, restore-specific write path exists) -- this
    replays exactly the sequence `ScheduleEditingService.restore` performs
    (load a historical snapshot via `get_version`, then persist it as a
    candidate) with a deliberately invalid `solver_status`, proving that
    path still rolls back completely with zero partial rows, the same
    guarantee `test_persist_edited_version_rolls_back_completely_on_integrity_violation`
    already proves for the ordinary move/lock/unlock path."""
    session, session_factory = db
    problem, _result, repo, v1 = _seed_and_persist_v1(session, session_factory)
    index = ProblemIndex(problem)
    move1 = _find_move(problem, index, Schedule(entries=v1.entries))
    v2 = repo.persist_edited_version(
        problem.school.id, problem.academic_year.id, 1, apply_move(problem, Schedule(entries=v1.entries), move1),
        v1.solver_status, v1.total_soft_penalty, 0.4, None,
    )

    source = repo.get_version(problem.school.id, problem.academic_year.id, 1)
    assert source is not None
    candidate = Schedule(entries=source.entries, locked_occurrences=source.locked_occurrences)

    year_id = session.execute(
        select(m.AcademicYear.id).where(m.AcademicYear.natural_id == problem.academic_year.id)
    ).scalar_one()
    before = _counts(session, year_id)

    with pytest.raises(IntegrityError) as exc_info:
        repo.persist_edited_version(
            problem.school.id, problem.academic_year.id, 2, candidate,
            SolverStatus.INFEASIBLE,  # never a valid persisted status
            source.total_soft_penalty, 0.0, None,
        )
    assert exc_info.value.orig.diag.constraint_name == "ck_schedule_version_solver_status"

    assert _counts(session, year_id) == before  # no orphan version_3 row, no partial children
    schedule_row = session.execute(select(m.Schedule).where(m.Schedule.academic_year_id == year_id)).scalar_one()
    v2_row = _version_row(session, year_id, 2)
    assert schedule_row.active_version_id == v2_row.id  # never repointed

    reloaded = repo.get_active_schedule(problem.school.id, problem.academic_year.id)
    assert reloaded == v2


# == persist_regenerated_version (Safe Configuration Changes, Slice C) ======
#
# `db` (SAVEPOINT-nested) is used for every scenario that is a genuinely
# SEQUENTIAL check -- "did something change before this call" -- exactly
# the same fixture `test_persist_edited_version_rejects_stale_base_with_zero_mutation`
# above already uses for the closely analogous stale-active-version case.
# `real_regen_setup` (real, separately COMMITTED transactions, never
# rolled back) is used for every one of the four CONCURRENCY REQUIREMENTS
# (A-D): a genuinely separate, already-committed intervening change, or
# (D) two truly simultaneous threads racing the same row lock -- neither
# of which the SAVEPOINT-nested fixture (one single shared, never-
# committed transaction) can represent.


def _ay_row(session: Session, year_id: int) -> m.AcademicYear:
    return session.execute(select(m.AcademicYear).where(m.AcademicYear.id == year_id)).scalar_one()


def _revision_row(session: Session, revision_id: int) -> m.ConfigurationRevision:
    return session.get(m.ConfigurationRevision, revision_id)


def _surrogate_id(session: Session, model: type, natural_id: str, year_id: int, revision_id: int) -> int:
    return session.execute(
        select(model.id).where(
            model.academic_year_id == year_id,
            model.configuration_revision_id == revision_id,
            model.natural_id == natural_id,
        )
    ).scalar_one()


def _mark_teacher_unavailable(
    session: Session, year_id: int, revision_id: int, teacher_natural_id: str, day_id: str, period_id: str,
) -> None:
    """Adds one `TeacherAvailability` row scoped to `revision_id` ONLY
    (never touching the other, still-published revision's own rows) --
    the deliberate way these tests make a specific historical lock
    incompatible with the draft, mirroring
    `tests/test_lock_compatibility.py`'s own
    `test_teacher_unavailable_at_locked_slot_is_incompatible` scenario
    but against the real persistence layer. Caller flushes/commits."""
    teacher_surrogate = _surrogate_id(session, m.Teacher, teacher_natural_id, year_id, revision_id)
    day_surrogate = _surrogate_id(session, m.Day, day_id, year_id, revision_id)
    period_surrogate = _surrogate_id(session, m.Period, period_id, year_id, revision_id)
    next_ordinal = (session.execute(
        select(func.max(m.TeacherAvailability.ordinal)).where(
            m.TeacherAvailability.academic_year_id == year_id,
            m.TeacherAvailability.configuration_revision_id == revision_id,
        )
    ).scalar_one() or 0) + 1
    session.add(m.TeacherAvailability(
        academic_year_id=year_id, configuration_revision_id=revision_id,
        teacher_id=teacher_surrogate, day_id=day_surrogate, period_id=period_surrogate,
        status=AvailabilityStatus.UNAVAILABLE.value, ordinal=next_ordinal,
    ))


def _seed_v1_locked_and_draft(session: Session, session_factory):
    """v1 (fresh generate, zero locks) -> lock two plain, non-split,
    resource-free, no-fixed-placement FLEXIBLE requirements (`history_8b`,
    `math_8b`) to produce v2 with two `LockedOccurrence`s -> open a draft
    (revision 2, cloned unchanged from revision 1, Owner Decision #7: an
    unchanged draft is still a legitimate regeneration target)."""
    problem, result = _seed(session)
    schedule_repo = SqlAlchemyScheduleVersionRepository(session_factory)
    v1 = schedule_repo.persist_initial_version(
        problem.school.id, problem.academic_year.id, problem, result.entries, result.status,
        result.total_soft_penalty, wall_time_seconds=2.5, random_seed=None,
    )
    index = ProblemIndex(problem)
    schedule = Schedule(entries=v1.entries)

    history_entry = next(e for e in v1.entries if e.requirement_id == "history_8b")
    schedule = lock_occurrence(
        problem, schedule, "history_8b", history_entry.day_id, history_entry.period_id, index=index,
    )
    math_entry = next(e for e in v1.entries if e.requirement_id == "math_8b")
    schedule = lock_occurrence(
        problem, schedule, "math_8b", math_entry.day_id, math_entry.period_id, index=index,
    )

    v2 = schedule_repo.persist_edited_version(
        problem.school.id, problem.academic_year.id, 1, schedule,
        v1.solver_status, v1.total_soft_penalty, 0.1, None,
    )
    assert {k.requirement_id for k in v2.locked_occurrences} == {"history_8b", "math_8b"}

    year_id = session.execute(
        select(m.AcademicYear.id).where(m.AcademicYear.natural_id == problem.academic_year.id)
    ).scalar_one()

    config_repo = SqlAlchemyConfigurationRevisionRepository(session_factory)
    state = config_repo.begin_draft(problem.school.id, problem.academic_year.id)
    assert state.draft_revision_number == 2
    draft_revision_id = _ay_row(session, year_id).draft_revision_id

    history_key = OccurrenceKey("history_8b", history_entry.day_id, history_entry.period_id)
    math_key = OccurrenceKey("math_8b", math_entry.day_id, math_entry.period_id)

    return {
        "problem": problem, "schedule_repo": schedule_repo, "v1": v1, "v2": v2,
        "year_id": year_id, "draft_revision_id": draft_revision_id,
        "history_key": history_key, "math_key": math_key, "math_entry": math_entry,
    }


def test_persist_regenerated_version_no_locks_publishes_draft_and_creates_next_version(db):
    session, session_factory = db
    problem, result = _seed(session)
    schedule_repo = SqlAlchemyScheduleVersionRepository(session_factory)
    v1 = schedule_repo.persist_initial_version(
        problem.school.id, problem.academic_year.id, problem, result.entries, result.status,
        result.total_soft_penalty, wall_time_seconds=2.5, random_seed=None,
    )
    year_id = session.execute(
        select(m.AcademicYear.id).where(m.AcademicYear.natural_id == problem.academic_year.id)
    ).scalar_one()

    config_repo = SqlAlchemyConfigurationRevisionRepository(session_factory)
    config_repo.begin_draft(problem.school.id, problem.academic_year.id)
    draft_revision_id = _ay_row(session, year_id).draft_revision_id

    snapshot = SessionFactorySchedulingProblemRepository(session_factory).load_draft_snapshot(
        problem.school.id, problem.academic_year.id,
    )
    draft_problem = snapshot.problem
    assert draft_problem == problem  # unchanged clone
    regen_result = solve(draft_problem)
    assert regen_result.is_success
    classification = classify_locks(draft_problem, ProblemIndex(draft_problem), v1.entries, frozenset())
    assert classification.compatible_keys == frozenset()
    assert classification.incompatible == ()

    v2 = schedule_repo.persist_regenerated_version(
        problem.school.id, problem.academic_year.id, 1,
        draft_problem, regen_result.entries, frozenset(), frozenset(),
        regen_result.status, regen_result.total_soft_penalty, wall_time_seconds=1.0, random_seed=None,
        expected_draft_revision_id=snapshot.revision_id,
    )

    assert v2.version_number == 2
    assert v2.locked_occurrences == frozenset()
    assert v2.configuration_revision_number == 2
    assert v2.entries == regen_result.entries

    year_row = _ay_row(session, year_id)
    assert year_row.draft_revision_id is None
    assert year_row.published_revision_id == draft_revision_id
    assert _revision_row(session, draft_revision_id).status == "PUBLISHED"

    v1_row = _version_row(session, year_id, 1)
    v2_row = _version_row(session, year_id, 2)
    assert v2_row.configuration_revision_id == draft_revision_id
    assert v2_row.parent_version_id == v1_row.id
    schedule_row = session.execute(select(m.Schedule).where(m.Schedule.academic_year_id == year_id)).scalar_one()
    assert schedule_row.active_version_id == v2_row.id

    # The FIRST revision remains PUBLISHED and completely untouched.
    published_revision_row = session.execute(
        select(m.ConfigurationRevision).where(
            m.ConfigurationRevision.academic_year_id == year_id, m.ConfigurationRevision.revision_number == 1,
        )
    ).scalar_one()
    assert published_revision_row.status == "PUBLISHED"

    reloaded = schedule_repo.get_active_schedule(problem.school.id, problem.academic_year.id)
    assert reloaded == v2
    state = config_repo.get_state(problem.school.id, problem.academic_year.id)
    assert state.draft_revision_number is None
    assert state.configuration_locked is True
    assert state.timetable_out_of_date is False


def test_persist_regenerated_version_all_compatible_locks_are_carried_forward(db):
    session, session_factory = db
    setup = _seed_v1_locked_and_draft(session, session_factory)
    problem, schedule_repo, v2 = setup["problem"], setup["schedule_repo"], setup["v2"]

    snapshot = SessionFactorySchedulingProblemRepository(session_factory).load_draft_snapshot(
        problem.school.id, problem.academic_year.id,
    )
    draft_problem = snapshot.problem
    regen_result = solve(draft_problem)
    assert regen_result.is_success
    classification = classify_locks(
        draft_problem, ProblemIndex(draft_problem), v2.entries, v2.locked_occurrences,
    )
    assert classification.compatible_keys == {setup["history_key"], setup["math_key"]}
    assert classification.incompatible == ()

    v3 = schedule_repo.persist_regenerated_version(
        problem.school.id, problem.academic_year.id, 2,
        draft_problem, regen_result.entries, classification.compatible_keys, frozenset(),
        regen_result.status, regen_result.total_soft_penalty, wall_time_seconds=1.0, random_seed=None,
        expected_draft_revision_id=snapshot.revision_id,
    )

    assert v3.version_number == 3
    assert v3.locked_occurrences == {setup["history_key"], setup["math_key"]}

    v3_row = _version_row(session, setup["year_id"], 3)
    assert _counts_for_version(session, v3_row.id)["locked_occurrence"] == 2


def test_persist_regenerated_version_mixed_locks_only_compatible_persisted_with_confirmation(db):
    session, session_factory = db
    setup = _seed_v1_locked_and_draft(session, session_factory)
    problem, schedule_repo = setup["problem"], setup["schedule_repo"]
    year_id, draft_revision_id = setup["year_id"], setup["draft_revision_id"]

    _mark_teacher_unavailable(
        session, year_id, draft_revision_id, "t_math", setup["math_entry"].day_id, setup["math_entry"].period_id,
    )
    session.flush()

    snapshot = SessionFactorySchedulingProblemRepository(session_factory).load_draft_snapshot(
        problem.school.id, problem.academic_year.id,
    )
    draft_problem = snapshot.problem
    regen_result = solve(draft_problem)
    assert regen_result.is_success
    classification = classify_locks(
        draft_problem, ProblemIndex(draft_problem), setup["v2"].entries, setup["v2"].locked_occurrences,
    )
    assert classification.compatible_keys == {setup["history_key"]}
    assert len(classification.incompatible) == 1
    assert classification.incompatible[0].key == setup["math_key"]
    assert classification.incompatible[0].reason_code == "TEACHER_UNAVAILABLE_AT_SLOT"

    v3 = schedule_repo.persist_regenerated_version(
        problem.school.id, problem.academic_year.id, 2,
        draft_problem, regen_result.entries, classification.compatible_keys,
        frozenset({setup["math_key"]}),
        regen_result.status, regen_result.total_soft_penalty, wall_time_seconds=1.0, random_seed=None,
        expected_draft_revision_id=snapshot.revision_id,
    )

    assert v3.locked_occurrences == frozenset({setup["history_key"]})
    v3_row = _version_row(session, year_id, 3)
    v3_locks = session.execute(
        select(m.LockedOccurrence).where(m.LockedOccurrence.schedule_version_id == v3_row.id)
    ).scalars().all()
    assert len(v3_locks) == 1

    # Historical v2's own two lock rows are untouched.
    v2_row = _version_row(session, year_id, 2)
    assert _counts_for_version(session, v2_row.id)["locked_occurrence"] == 2


def test_persist_regenerated_version_raises_configuration_changed_when_expected_draft_is_no_longer_open(db):
    session, session_factory = db
    problem, result = _seed(session)
    schedule_repo = SqlAlchemyScheduleVersionRepository(session_factory)
    v1 = schedule_repo.persist_initial_version(
        problem.school.id, problem.academic_year.id, problem, result.entries, result.status,
        result.total_soft_penalty, wall_time_seconds=2.5, random_seed=None,
    )
    # No begin_draft call -- the initial draft was already published by
    # persist_initial_version above, and nothing reopened one.
    year_id = session.execute(
        select(m.AcademicYear.id).where(m.AcademicYear.natural_id == problem.academic_year.id)
    ).scalar_one()
    before = _counts(session, year_id)

    with pytest.raises(ConfigurationChangedDuringGenerationError) as exc_info:
        schedule_repo.persist_regenerated_version(
            problem.school.id, problem.academic_year.id, 1,
            problem, result.entries, frozenset(), frozenset(),
            result.status, result.total_soft_penalty, wall_time_seconds=1.0, random_seed=None,
            expected_draft_revision_id=_ay_row(session, year_id).published_revision_id,
        )
    assert exc_info.value.school_natural_id == problem.school.id
    assert exc_info.value.academic_year_natural_id == problem.academic_year.id

    assert _counts(session, year_id) == before
    schedule_row = session.execute(select(m.Schedule).where(m.Schedule.academic_year_id == year_id)).scalar_one()
    v1_row = _version_row(session, year_id, 1)
    assert schedule_row.active_version_id == v1_row.id
    reloaded = schedule_repo.get_active_schedule(problem.school.id, problem.academic_year.id)
    assert reloaded == v1


def test_persist_regenerated_version_version_number_is_max_plus_one_not_base_plus_one(db):
    """A directly-inserted, non-active 'future' ScheduleVersion row
    (version_number=5) -- structurally representable but never producible
    through today's linear-chain-only public API, exactly the same
    'construct an otherwise-unreachable row shape directly' convention
    Slice B's own defense-in-depth tests already use -- proves the
    query genuinely computes MAX(version_number)+1, not
    base_version_number+1 (which, for every reachable normal scenario,
    are numerically identical, since the active version is always the
    current maximum in this codebase's own linear-history design)."""
    session, session_factory = db
    setup = _seed_v1_locked_and_draft(session, session_factory)
    problem, schedule_repo, v2 = setup["problem"], setup["schedule_repo"], setup["v2"]
    year_id = setup["year_id"]

    v2_row = _version_row(session, year_id, 2)
    schedule_row = session.execute(select(m.Schedule).where(m.Schedule.academic_year_id == year_id)).scalar_one()
    session.add(m.ScheduleVersion(
        academic_year_id=year_id, schedule_id=schedule_row.id, version_number=5,
        parent_version_id=v2_row.parent_version_id, configuration_revision_id=v2_row.configuration_revision_id,
        solver_status=v2_row.solver_status, total_soft_penalty=v2_row.total_soft_penalty,
        wall_time_seconds=v2_row.wall_time_seconds, random_seed=None,
    ))
    session.flush()  # schedule_row.active_version_id still points at v2 -- this new row is not active

    snapshot = SessionFactorySchedulingProblemRepository(session_factory).load_draft_snapshot(
        problem.school.id, problem.academic_year.id,
    )
    draft_problem = snapshot.problem
    regen_result = solve(draft_problem)
    classification = classify_locks(
        draft_problem, ProblemIndex(draft_problem), v2.entries, v2.locked_occurrences,
    )

    v_new = schedule_repo.persist_regenerated_version(
        problem.school.id, problem.academic_year.id, 2,  # base is still 2, the real active version
        draft_problem, regen_result.entries, classification.compatible_keys, frozenset(),
        regen_result.status, regen_result.total_soft_penalty, wall_time_seconds=1.0, random_seed=None,
        expected_draft_revision_id=snapshot.revision_id,
    )

    assert v_new.version_number == 6  # MAX(1, 2, 5) + 1, never 2 + 1 == 3


def test_persist_regenerated_version_historical_data_remains_unchanged(db):
    session, session_factory = db
    setup = _seed_v1_locked_and_draft(session, session_factory)
    problem, schedule_repo = setup["problem"], setup["schedule_repo"]
    v1, v2 = setup["v1"], setup["v2"]
    year_id = setup["year_id"]

    v1_row_before = _version_row(session, year_id, 1)
    v2_row_before = _version_row(session, year_id, 2)
    v1_counts_before = _counts_for_version(session, v1_row_before.id)
    v2_counts_before = _counts_for_version(session, v2_row_before.id)
    published_row_before = session.execute(
        select(m.ConfigurationRevision).where(
            m.ConfigurationRevision.academic_year_id == year_id, m.ConfigurationRevision.revision_number == 1,
        )
    ).scalar_one()
    requirement_rows_before = list(session.execute(
        select(m.TeachingRequirement).where(
            m.TeachingRequirement.academic_year_id == year_id,
            m.TeachingRequirement.configuration_revision_id == published_row_before.id,
        )
    ).scalars())

    snapshot = SessionFactorySchedulingProblemRepository(session_factory).load_draft_snapshot(
        problem.school.id, problem.academic_year.id,
    )
    draft_problem = snapshot.problem
    regen_result = solve(draft_problem)
    classification = classify_locks(
        draft_problem, ProblemIndex(draft_problem), v2.entries, v2.locked_occurrences,
    )
    schedule_repo.persist_regenerated_version(
        problem.school.id, problem.academic_year.id, 2,
        draft_problem, regen_result.entries, classification.compatible_keys, frozenset(),
        regen_result.status, regen_result.total_soft_penalty, wall_time_seconds=1.0, random_seed=None,
        expected_draft_revision_id=snapshot.revision_id,
    )

    assert _version_row(session, year_id, 1) == v1_row_before
    assert _version_row(session, year_id, 2) == v2_row_before
    assert _counts_for_version(session, v1_row_before.id) == v1_counts_before
    assert _counts_for_version(session, v2_row_before.id) == v2_counts_before
    published_row_after = _revision_row(session, published_row_before.id)
    assert published_row_after.status == "PUBLISHED"
    requirement_rows_after = list(session.execute(
        select(m.TeachingRequirement).where(
            m.TeachingRequirement.academic_year_id == year_id,
            m.TeachingRequirement.configuration_revision_id == published_row_before.id,
        )
    ).scalars())
    assert len(requirement_rows_after) == len(requirement_rows_before)


def test_persist_regenerated_version_missing_incompatible_confirmation_raises_with_fresh_data(db):
    session, session_factory = db
    setup = _seed_v1_locked_and_draft(session, session_factory)
    problem, schedule_repo = setup["problem"], setup["schedule_repo"]
    year_id, draft_revision_id = setup["year_id"], setup["draft_revision_id"]

    _mark_teacher_unavailable(
        session, year_id, draft_revision_id, "t_math", setup["math_entry"].day_id, setup["math_entry"].period_id,
    )
    session.flush()

    snapshot = SessionFactorySchedulingProblemRepository(session_factory).load_draft_snapshot(
        problem.school.id, problem.academic_year.id,
    )
    draft_problem = snapshot.problem
    regen_result = solve(draft_problem)
    before = _counts(session, year_id)

    with pytest.raises(IncompatibleLocksRequireConfirmationError) as exc_info:
        schedule_repo.persist_regenerated_version(
            problem.school.id, problem.academic_year.id, 2,
            draft_problem, regen_result.entries, frozenset({setup["history_key"]}),
            frozenset(),  # never confirmed -- caller acted as if everything were compatible
            regen_result.status, regen_result.total_soft_penalty, wall_time_seconds=1.0, random_seed=None,
            expected_draft_revision_id=snapshot.revision_id,
        )

    fresh = exc_info.value.incompatible_locks
    assert len(fresh) == 1
    assert fresh[0].key == setup["math_key"]
    assert fresh[0].reason_code == "TEACHER_UNAVAILABLE_AT_SLOT"

    assert _counts(session, year_id) == before
    assert _ay_row(session, year_id).draft_revision_id == draft_revision_id
    assert _revision_row(session, draft_revision_id).status == "DRAFT"
    schedule_row = session.execute(select(m.Schedule).where(m.Schedule.academic_year_id == year_id)).scalar_one()
    v2_row = _version_row(session, year_id, 2)
    assert schedule_row.active_version_id == v2_row.id


def test_persist_regenerated_version_caller_compatible_set_mismatch_is_refused_internally(db):
    """Defense-in-depth (10): even after (9) proves the confirmed-
    incompatible set is exactly right, a caller-supplied `locked_occurrences`
    that disagrees with the authoritative `classify_locks` result is an
    internal contract violation -- never a legitimate concurrent-state
    race (that is fully absorbed by (9) alone) -- and is refused with a
    bare `RuntimeError`, matching `SqlAlchemyConfigurationRevisionRepository.
    discard_draft`'s own established defense-in-depth convention."""
    session, session_factory = db
    setup = _seed_v1_locked_and_draft(session, session_factory)
    problem, schedule_repo = setup["problem"], setup["schedule_repo"]
    year_id, draft_revision_id = setup["year_id"], setup["draft_revision_id"]

    _mark_teacher_unavailable(
        session, year_id, draft_revision_id, "t_math", setup["math_entry"].day_id, setup["math_entry"].period_id,
    )
    session.flush()

    snapshot = SessionFactorySchedulingProblemRepository(session_factory).load_draft_snapshot(
        problem.school.id, problem.academic_year.id,
    )
    draft_problem = snapshot.problem
    regen_result = solve(draft_problem)
    before = _counts(session, year_id)

    with pytest.raises(RuntimeError) as exc_info:
        schedule_repo.persist_regenerated_version(
            problem.school.id, problem.academic_year.id, 2,
            draft_problem, regen_result.entries,
            frozenset(),  # WRONG -- should be {history_key}; (9) alone already passed
            frozenset({setup["math_key"]}),  # correctly confirmed
            regen_result.status, regen_result.total_soft_penalty, wall_time_seconds=1.0, random_seed=None,
            expected_draft_revision_id=snapshot.revision_id,
        )
    # Not IncompatibleLocksRequireConfirmationError, not
    # ConfigurationChangedDuringGenerationError, not StaleScheduleVersionError
    # -- a bare RuntimeError specifically, disambiguated by message.
    assert not isinstance(exc_info.value, IncompatibleLocksRequireConfirmationError)
    assert "does not match the authoritative compatible-lock classification" in str(exc_info.value)

    assert _counts(session, year_id) == before
    assert _ay_row(session, year_id).draft_revision_id == draft_revision_id
    assert _revision_row(session, draft_revision_id).status == "DRAFT"


def test_persist_regenerated_version_rolls_back_completely_on_integrity_violation(db):
    """The atomic-failure proof: an invalid `solver_status` trips
    `ck_schedule_version_solver_status` INSIDE the write sequence, AFTER
    the draft-publish flush (11) has already happened but before
    `session.commit()` (16) -- proving the publish transition itself is
    rolled back along with everything after it, never left half-applied.
    Reuses the exact same real, unmodified production constraint
    `test_persist_edited_version_rolls_back_completely_on_integrity_violation`
    already uses -- no test-only monkeypatch, no weakened transaction
    boundary."""
    session, session_factory = db
    setup = _seed_v1_locked_and_draft(session, session_factory)
    problem, schedule_repo = setup["problem"], setup["schedule_repo"]
    year_id, draft_revision_id = setup["year_id"], setup["draft_revision_id"]

    snapshot = SessionFactorySchedulingProblemRepository(session_factory).load_draft_snapshot(
        problem.school.id, problem.academic_year.id,
    )
    draft_problem = snapshot.problem
    regen_result = solve(draft_problem)
    classification = classify_locks(
        draft_problem, ProblemIndex(draft_problem), setup["v2"].entries, setup["v2"].locked_occurrences,
    )
    before = _counts(session, year_id)

    with pytest.raises(IntegrityError) as exc_info:
        schedule_repo.persist_regenerated_version(
            problem.school.id, problem.academic_year.id, 2,
            draft_problem, regen_result.entries, classification.compatible_keys, frozenset(),
            SolverStatus.INFEASIBLE,  # never a valid persisted status
            regen_result.total_soft_penalty, wall_time_seconds=1.0, random_seed=None,
            expected_draft_revision_id=snapshot.revision_id,
        )
    assert exc_info.value.orig.diag.constraint_name == "ck_schedule_version_solver_status"

    # The draft publish transition (11) itself was rolled back -- not
    # just the later ScheduleVersion/entry inserts.
    assert _ay_row(session, year_id).draft_revision_id == draft_revision_id
    assert _ay_row(session, year_id).published_revision_id != draft_revision_id
    assert _revision_row(session, draft_revision_id).status == "DRAFT"

    assert _counts(session, year_id) == before  # no orphan version_3 row, no partial children
    schedule_row = session.execute(select(m.Schedule).where(m.Schedule.academic_year_id == year_id)).scalar_one()
    v2_row = _version_row(session, year_id, 2)
    assert schedule_row.active_version_id == v2_row.id  # never repointed

    reloaded = schedule_repo.get_active_schedule(problem.school.id, problem.academic_year.id)
    assert reloaded == setup["v2"]


# == Real-PostgreSQL concurrency proofs (Owner Decision #36) =================
#
# `real_regen_setup` mirrors `tests_web/test_configuration_revision_repository.py`'s
# own `seeded_and_published_db` fixture exactly, for the identical reason:
# these four scenarios need genuinely separate, ALREADY-COMMITTED
# transactions (A, B, C) or truly simultaneous ones (D) -- something the
# SAVEPOINT-nested `db` fixture, which pins every test to one single
# shared, never-committed transaction, cannot represent.

@pytest.fixture
def real_regen_setup(live_db_engine):
    connection = live_db_engine.connect()
    session = Session(bind=connection)
    problem = build_valid_fixture()
    write_scheduling_problem(session, problem)
    session.commit()
    session.close()
    connection.close()

    session_factory = sessionmaker(bind=live_db_engine, autoflush=False, autocommit=False, expire_on_commit=False)

    generate_service = GenerateScheduleService(
        SessionFactorySchedulingProblemRepository(session_factory),
        SqlAlchemyScheduleVersionRepository(session_factory),
    )
    v1 = generate_service.generate(
        problem.school.id, problem.academic_year.id,
        solver_options=SolverOptions(random_seed=11, num_search_workers=1),
    )
    assert v1.solver_status in (SolverStatus.OPTIMAL, SolverStatus.FEASIBLE)

    # Deliberately does NOT open a draft here. `persist_edited_version`'s
    # own natural-id-to-surrogate lookup (like `persist_initial_version`'s)
    # is unscoped by revision and only ever safe to call while at most
    # one revision exists for this year -- exactly the invariant
    # `ScheduleEditingService`'s own `_reject_if_out_of_date` guard
    # enforces at the application layer whenever a draft IS open. A test
    # that needs a SECOND ScheduleVersion (via a direct, guard-bypassing
    # repository call) must create it BEFORE opening a draft, mirroring
    # the real, legitimate application order (edit first, open a draft
    # to change configuration later) -- each test below opens its own
    # draft, via `SqlAlchemyConfigurationRevisionRepository.begin_draft`,
    # only after any such edit.
    try:
        yield problem, session_factory, v1
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


def test_persist_regenerated_version_raises_configuration_changed_when_draft_edited_after_solve(
    real_regen_setup, live_db_engine,
):
    """A: a genuinely separate, REAL, committed configuration write lands
    in the draft after the caller's own load+solve but before this call's
    persistence -- exactly the race Owner Decision #36 exists to close."""
    problem, session_factory, v1 = real_regen_setup
    schedule_repo = SqlAlchemyScheduleVersionRepository(session_factory)
    problem_repo = SessionFactorySchedulingProblemRepository(session_factory)
    SqlAlchemyConfigurationRevisionRepository(session_factory).begin_draft(
        problem.school.id, problem.academic_year.id,
    )

    snapshot = problem_repo.load_draft_snapshot(problem.school.id, problem.academic_year.id)
    draft_problem = snapshot.problem
    regen_result = solve(draft_problem)
    assert regen_result.is_success

    intervening_connection = live_db_engine.connect()
    intervening_session = Session(bind=intervening_connection)
    year_id = intervening_session.execute(
        select(m.AcademicYear.id).where(m.AcademicYear.natural_id == problem.academic_year.id)
    ).scalar_one()
    draft_revision_id = _ay_row(intervening_session, year_id).draft_revision_id
    intervening_session.add(m.Teacher(
        academic_year_id=year_id, configuration_revision_id=draft_revision_id,
        natural_id="t_intervening", first_name="Intervening", last_name="", ordinal=999,
    ))
    intervening_session.commit()
    intervening_session.close()
    intervening_connection.close()

    check_connection = live_db_engine.connect()
    check_session = Session(bind=check_connection)
    before = _counts(check_session, year_id)
    check_session.close()
    check_connection.close()

    with pytest.raises(ConfigurationChangedDuringGenerationError):
        schedule_repo.persist_regenerated_version(
            problem.school.id, problem.academic_year.id, v1.version_number,
            draft_problem, regen_result.entries, frozenset(), frozenset(),
            regen_result.status, regen_result.total_soft_penalty, wall_time_seconds=1.0, random_seed=None,
            expected_draft_revision_id=snapshot.revision_id,
        )

    check_connection2 = live_db_engine.connect()
    check_session2 = Session(bind=check_connection2)
    try:
        assert _counts(check_session2, year_id) == before
        year_row = _ay_row(check_session2, year_id)
        assert year_row.draft_revision_id == draft_revision_id
        assert _revision_row(check_session2, draft_revision_id).status == "DRAFT"
    finally:
        check_session2.close()
        check_connection2.close()


def test_persist_regenerated_version_raises_stale_base_version_when_active_changed(real_regen_setup):
    """B: a genuinely separate, REAL, committed edit promotes v2 to
    active while the caller still believes v1 is current -- the identical
    scenario `test_persist_edited_version_rejects_stale_base_with_zero_mutation`
    already proves for plain editing, now proven for regeneration too."""
    problem, session_factory, v1 = real_regen_setup
    schedule_repo = SqlAlchemyScheduleVersionRepository(session_factory)
    problem_repo = SessionFactorySchedulingProblemRepository(session_factory)

    # The edit (creating v2) happens FIRST, while only one revision
    # exists -- mirroring the real, legitimate application order (see
    # `real_regen_setup`'s own comment). The draft is opened only after.
    index = ProblemIndex(problem)
    move = _find_move(problem, index, Schedule(entries=v1.entries))
    candidate = apply_move(problem, Schedule(entries=v1.entries), move)
    schedule_repo.persist_edited_version(
        problem.school.id, problem.academic_year.id, 1, candidate,
        v1.solver_status, v1.total_soft_penalty, 0.2, None,
    )

    SqlAlchemyConfigurationRevisionRepository(session_factory).begin_draft(
        problem.school.id, problem.academic_year.id,
    )
    snapshot = problem_repo.load_draft_snapshot(problem.school.id, problem.academic_year.id)
    draft_problem = snapshot.problem
    regen_result = solve(draft_problem)

    with pytest.raises(StaleScheduleVersionError) as exc_info:
        schedule_repo.persist_regenerated_version(
            problem.school.id, problem.academic_year.id, 1,  # stale -- active is now 2
            draft_problem, regen_result.entries, frozenset(), frozenset(),
            regen_result.status, regen_result.total_soft_penalty, wall_time_seconds=1.0, random_seed=None,
            expected_draft_revision_id=snapshot.revision_id,
        )
    assert exc_info.value.expected_base_version_number == 1
    assert exc_info.value.actual_active_version_number == 2

    reloaded = schedule_repo.get_active_schedule(problem.school.id, problem.academic_year.id)
    assert reloaded.version_number == 2


def test_persist_regenerated_version_raises_incompatible_locks_when_compatibility_changes_after_caller_classified(
    real_regen_setup, live_db_engine,
):
    """C: the caller classifies locks while the draft is still unchanged
    (math_8b compatible) and confirms nothing -- a genuinely separate,
    REAL, committed draft edit then makes that exact lock incompatible.
    The caller DOES correctly reload/re-solve against the now-current
    draft (so step (3)-(4)'s configuration-changed check does not fire --
    this is deliberately NOT scenario A) but its own
    `confirmed_incompatible_lock_keys` still reflects the earlier,
    now-stale classification. Persistence must recompute lock
    compatibility fresh against the reloaded active version and draft,
    never trust that stale confirmation."""
    problem, session_factory, v1 = real_regen_setup
    schedule_repo = SqlAlchemyScheduleVersionRepository(session_factory)
    problem_repo = SessionFactorySchedulingProblemRepository(session_factory)

    index = ProblemIndex(problem)
    math_entry = next(e for e in v1.entries if e.requirement_id == "math_8b")
    locked_schedule = lock_occurrence(
        problem, Schedule(entries=v1.entries), "math_8b", math_entry.day_id, math_entry.period_id, index=index,
    )
    v2 = schedule_repo.persist_edited_version(
        problem.school.id, problem.academic_year.id, 1, locked_schedule,
        v1.solver_status, v1.total_soft_penalty, 0.1, None,
    )
    math_key = OccurrenceKey("math_8b", math_entry.day_id, math_entry.period_id)
    assert v2.locked_occurrences == frozenset({math_key})

    SqlAlchemyConfigurationRevisionRepository(session_factory).begin_draft(
        problem.school.id, problem.academic_year.id,
    )
    snapshot = problem_repo.load_draft_snapshot(problem.school.id, problem.academic_year.id)
    draft_problem_before_edit = snapshot.problem
    classification_before_edit = classify_locks(
        draft_problem_before_edit, ProblemIndex(draft_problem_before_edit), v2.entries, v2.locked_occurrences,
    )
    assert classification_before_edit.compatible_keys == frozenset({math_key})
    assert classification_before_edit.incompatible == ()

    intervening_connection = live_db_engine.connect()
    intervening_session = Session(bind=intervening_connection)
    year_id = intervening_session.execute(
        select(m.AcademicYear.id).where(m.AcademicYear.natural_id == problem.academic_year.id)
    ).scalar_one()
    draft_revision_id = _ay_row(intervening_session, year_id).draft_revision_id
    _mark_teacher_unavailable(
        intervening_session, year_id, draft_revision_id, "t_math", math_entry.day_id, math_entry.period_id,
    )
    intervening_session.commit()
    intervening_session.close()
    intervening_connection.close()

    # The caller correctly reloads/re-solves against the NOW-current
    # draft (so `problem` matches what persistence will independently
    # reload too) but still passes its OLD, now-stale
    # confirmed_incompatible_lock_keys (empty) and OLD compatible-lock
    # set -- exactly a caller that classified once and never re-checked
    # before finally calling persist_regenerated_version.
    snapshot = problem_repo.load_draft_snapshot(problem.school.id, problem.academic_year.id)
    draft_problem_after_edit = snapshot.problem
    regen_result = solve(draft_problem_after_edit)

    with pytest.raises(IncompatibleLocksRequireConfirmationError) as exc_info:
        schedule_repo.persist_regenerated_version(
            problem.school.id, problem.academic_year.id, 2,
            draft_problem_after_edit, regen_result.entries, classification_before_edit.compatible_keys,
            frozenset(),  # the caller's now-stale confirmation: still empty
            regen_result.status, regen_result.total_soft_penalty, wall_time_seconds=1.0, random_seed=None,
            expected_draft_revision_id=snapshot.revision_id,
        )
    fresh = exc_info.value.incompatible_locks
    assert len(fresh) == 1
    assert fresh[0].key == math_key
    assert fresh[0].reason_code == "TEACHER_UNAVAILABLE_AT_SLOT"

    check_connection = live_db_engine.connect()
    check_session = Session(bind=check_connection)
    try:
        assert _ay_row(check_session, year_id).draft_revision_id == draft_revision_id
        assert _revision_row(check_session, draft_revision_id).status == "DRAFT"
    finally:
        check_session.close()
        check_connection.close()


def test_concurrent_persist_regenerated_version_exactly_one_succeeds(real_regen_setup, live_db_engine):
    """D: two threads race to regenerate the same year/base/draft at the
    same instant. Whichever thread's row lock wins publishes the draft
    and creates v2; the other blocks until the first commits, reloads,
    finds the draft already gone, and fails cleanly with
    `ConfigurationChangedDuringGenerationError` -- never a double publish, never two
    ScheduleVersions claiming the same version_number. Mirrors
    `tests_web/test_configuration_revision_repository.py::
    test_concurrent_begin_draft_converges_on_exactly_one_draft`'s exact
    `threading.Barrier` technique."""
    problem, session_factory, v1 = real_regen_setup
    schedule_repo = SqlAlchemyScheduleVersionRepository(session_factory)
    problem_repo = SessionFactorySchedulingProblemRepository(session_factory)
    SqlAlchemyConfigurationRevisionRepository(session_factory).begin_draft(
        problem.school.id, problem.academic_year.id,
    )

    snapshot = problem_repo.load_draft_snapshot(problem.school.id, problem.academic_year.id)
    draft_problem = snapshot.problem
    regen_result = solve(draft_problem)
    assert regen_result.is_success

    barrier = threading.Barrier(2)
    results: dict[str, object] = {}

    def attempt(label: str) -> None:
        barrier.wait(timeout=10)
        try:
            results[label] = schedule_repo.persist_regenerated_version(
                problem.school.id, problem.academic_year.id, v1.version_number,
                draft_problem, regen_result.entries, frozenset(), frozenset(),
                regen_result.status, regen_result.total_soft_penalty, wall_time_seconds=1.0, random_seed=None,
                expected_draft_revision_id=snapshot.revision_id,
            )
        except Exception as exc:  # pragma: no cover - diagnostic only
            results[label] = exc

    t1 = threading.Thread(target=attempt, args=("A",))
    t2 = threading.Thread(target=attempt, args=("B",))
    t1.start()
    t2.start()
    t1.join(timeout=30)
    t2.join(timeout=30)

    successes = [r for r in results.values() if not isinstance(r, Exception)]
    failures = [r for r in results.values() if isinstance(r, Exception)]
    assert len(successes) == 1, f"expected exactly one success, got: {results!r}"
    assert len(failures) == 1, f"expected exactly one clean failure, got: {results!r}"
    assert isinstance(failures[0], ConfigurationChangedDuringGenerationError), (
        f"loser must fail with ConfigurationChangedDuringGenerationError after the winner already "
        f"published the draft, got: {failures[0]!r}"
    )
    assert successes[0].version_number == 2

    check_connection = live_db_engine.connect()
    check_session = Session(bind=check_connection)
    try:
        year_id = check_session.execute(
            select(m.AcademicYear.id).where(m.AcademicYear.natural_id == problem.academic_year.id)
        ).scalar_one()
        year_row = _ay_row(check_session, year_id)
        assert year_row.draft_revision_id is None

        version_rows = check_session.execute(
            select(m.ScheduleVersion).where(m.ScheduleVersion.academic_year_id == year_id)
        ).scalars().all()
        assert sorted(v.version_number for v in version_rows) == [1, 2]  # never a duplicate/orphan

        schedule_row = check_session.execute(
            select(m.Schedule).where(m.Schedule.academic_year_id == year_id)
        ).scalar_one()
        v2_row = next(v for v in version_rows if v.version_number == 2)
        assert schedule_row.active_version_id == v2_row.id

        revision_rows = check_session.execute(
            select(m.ConfigurationRevision).where(m.ConfigurationRevision.academic_year_id == year_id)
        ).scalars().all()
        assert len(revision_rows) == 2  # published(1) + newly-published(2), never a third
        assert all(r.status == "PUBLISHED" for r in revision_rows)
    finally:
        check_session.close()
        check_connection.close()


def test_regeneration_rejects_identical_reopened_draft_with_reused_number(real_regen_setup):
    problem, session_factory, v1 = real_regen_setup
    config = SqlAlchemyConfigurationRevisionRepository(session_factory)
    problems = SessionFactorySchedulingProblemRepository(session_factory)
    repo = SqlAlchemyScheduleVersionRepository(session_factory)
    school, year = problem.school.id, problem.academic_year.id
    original_state = config.begin_draft(school, year)
    snapshot = problems.load_draft_snapshot(school, year)
    result = solve(snapshot.problem)
    assert result.is_success

    # These committed operations also prove the snapshot reader released its lock.
    config.discard_draft(school, year)
    replacement_state = config.begin_draft(school, year)
    replacement = problems.load_draft_snapshot(school, year)
    assert replacement_state.draft_revision_number == original_state.draft_revision_number == 2
    assert replacement.revision_id != snapshot.revision_id
    assert replacement.problem == snapshot.problem
    with session_factory() as session:
        year_id = session.execute(select(m.AcademicYear.id).where(m.AcademicYear.natural_id == year)).scalar_one()
        published_id = _ay_row(session, year_id).published_revision_id
        before = _counts(session, year_id)
    history_before = repo.get_version(school, year, v1.version_number)

    with pytest.raises(ConfigurationChangedDuringGenerationError):
        repo.persist_regenerated_version(
            school, year, v1.version_number, snapshot.problem, result.entries,
            frozenset(), frozenset(), result.status, result.total_soft_penalty, 1.0, None,
            expected_draft_revision_id=snapshot.revision_id,
        )

    with session_factory() as session:
        assert _counts(session, year_id) == before
        ay = _ay_row(session, year_id)
        assert ay.draft_revision_id == replacement.revision_id
        assert ay.published_revision_id == published_id
        assert session.get(m.ConfigurationRevision, replacement.revision_id).status == "DRAFT"
        assert session.get(m.ConfigurationRevision, published_id).status == "PUBLISHED"
    assert repo.get_active_schedule(school, year) == v1
    assert repo.get_version(school, year, v1.version_number) == history_before


@pytest.mark.parametrize("historical_last", [False, True])
def test_edits_after_regeneration_scope_all_references_to_base_revision(real_regen_setup, historical_last):
    from sqlalchemy import event
    from school_timetable.application.schedule_editing_service import ScheduleEditingService

    problem, session_factory, v1 = real_regen_setup
    school, year = problem.school.id, problem.academic_year.id
    repo = SqlAlchemyScheduleVersionRepository(session_factory)
    problems = SessionFactorySchedulingProblemRepository(session_factory)
    config = SqlAlchemyConfigurationRevisionRepository(session_factory)
    config.begin_draft(school, year)
    regenerated = GenerateScheduleService(problems, repo, config).regenerate(school, year, v1.version_number)
    editing = ScheduleEditingService(problems, repo, config)
    history_before = [replace(repo.get_version(school, year, n), is_active=False) for n in (1, 2)]
    models = (m.Day, m.Period, m.TeachingRequirement, m.ReservedBlock)
    with session_factory() as session:
        year_id = session.execute(select(m.AcademicYear.id).where(m.AcademicYear.natural_id == year)).scalar_one()
        revision_id = _version_row(session, year_id, regenerated.version_number).configuration_revision_id
        for model in models:
            rows = session.execute(select(model).where(model.academic_year_id == year_id)).scalars().all()
            assert rows  # Cover reserved-block references as well as lesson references.
            grouped = {}
            for row in rows:
                grouped.setdefault(row.natural_id, []).append(row)
            assert all(len({r.configuration_revision_id for r in group}) == 2 for group in grouped.values())
            assert all(len({r.id for r in group}) == 2 for group in grouped.values())

    def order_configuration_queries(state):
        if not state.is_select:
            return
        descriptions = getattr(state.statement, "column_descriptions", ())
        if len(descriptions) == 1:
            model = descriptions[0].get("entity")
            if model in models:
                column = model.configuration_revision_id
                state.statement = state.statement.order_by(None).order_by(
                    column.desc() if historical_last else column.asc(), model.id,
                )

    def assert_references(active):
        with session_factory() as session:
            version = _version_row(session, year_id, active.version_number)
            assert version.configuration_revision_id == revision_id
            entries = session.execute(select(m.ScheduleEntry).where(m.ScheduleEntry.schedule_version_id == version.id)).scalars().all()
            locks = session.execute(select(m.LockedOccurrence).where(m.LockedOccurrence.schedule_version_id == version.id)).scalars().all()
            assert entries
            for row in entries:
                references = [(m.Day, row.day_id), (m.Period, row.period_id),
                              (m.TeachingRequirement, row.teaching_requirement_id), (m.ReservedBlock, row.reserved_block_id)]
                for model, identity in references:
                    if identity is not None:
                        assert session.get(model, identity).configuration_revision_id == revision_id
            assert len(locks) == len(active.locked_occurrences)
            for row in locks:
                for model, identity in [(m.Day, row.day_id), (m.Period, row.anchor_period_id),
                                        (m.TeachingRequirement, row.teaching_requirement_id)]:
                    assert session.get(model, identity).configuration_revision_id == revision_id
        assert repo.get_active_schedule(school, year) == active

    event.listen(Session, "do_orm_execute", order_configuration_queries)
    try:
        entry = next(e for e in regenerated.entries if e.requirement_id == "history_8b")
        locked = editing.lock(school, year, regenerated.version_number, entry.requirement_id, entry.day_id, entry.period_id)
        assert locked.locked_occurrences
        assert_references(locked)
        optimized = editing.reoptimize(school, year, locked.version_number)
        assert optimized.locked_occurrences == locked.locked_occurrences
        assert_references(optimized)
        unlocked = editing.unlock(school, year, optimized.version_number, entry.requirement_id, entry.day_id, entry.period_id)
        assert not unlocked.locked_occurrences
        assert_references(unlocked)
        restored = editing.restore(school, year, unlocked.version_number, locked.version_number)
        assert restored.locked_occurrences == locked.locked_occurrences
        assert_references(restored)
        # Find a legal move using the existing validation logic, not a solver-layout assumption.
        candidate = Schedule(entries=unlocked.entries)
        for source in unlocked.entries:
            if source.source != EntrySource.REQUIREMENT:
                continue
            target = next(((day.id, period.id) for day in problem.days for period in problem.periods
                           if (day.id, period.id) != (source.day_id, source.period_id)
                           and validate_move(problem, candidate, source.requirement_id, source.day_id,
                                             source.period_id, day.id, period.id).allowed), None)
            if target:
                break
        assert target is not None
        # Restore the unlocked version first so the candidate above matches the active locks.
        active = editing.restore(school, year, restored.version_number, unlocked.version_number)
        assert_references(active)
        moved = editing.move(school, year, active.version_number, source.requirement_id,
                             source.day_id, source.period_id, *target)
        assert_references(moved)
    finally:
        event.remove(Session, "do_orm_execute", order_configuration_queries)
    assert [repo.get_version(school, year, n) for n in (1, 2)] == history_before
