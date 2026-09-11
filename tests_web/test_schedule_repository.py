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

from dataclasses import replace

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from school_timetable.application.errors import (
    ScheduleAlreadyExistsError,
    SchedulingProblemNotFoundError,
    StaleScheduleVersionError,
)
from school_timetable.domain.indexing import ProblemIndex
from school_timetable.domain.result import EntrySource, SolverStatus
from school_timetable.domain.schedule import Schedule
from school_timetable.fixtures.valid_fixture import build_valid_fixture
from school_timetable.persistence import models as m
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
    candidate `Schedule`."""
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
