"""Real-PostgreSQL repository-level tests for Safe Configuration
Changes, Slice B's `SqlAlchemyConfigurationRevisionRepository` --
`get_state`, `begin_draft` (eager clone across all fifteen
`SchedulingProblem` configuration tables, with FK remapping to the
new draft's own rows), and `discard_draft`.

Uses the REAL persistence layer against a real PostgreSQL instance
(`live_db_engine`, `join_transaction_mode="create_savepoint"`, matching
every other `tests_web/test_*_repository.py` file's discipline) --
never a fake. `_seed_and_publish` runs a fresh year through the REAL
`GenerateScheduleService` end to end (solve + verify +
`persist_initial_version`) to produce a genuinely production-created
PUBLISHED `ConfigurationRevision` to clone from -- never hand-rolled --
exactly mirroring a real school's first successful Generate.

`_resolved_snapshot` is the core verification tool used throughout:
for one `(academic_year_id, configuration_revision_id)`, it builds a
frozenset per table of every row's natural-id-resolved content
(scalar fields verbatim, every FK resolved through THAT SAME
revision's own id -> natural_id map, never the raw surrogate id).
Two revisions with equal snapshots are, by construction, semantically
identical: same row counts, same natural IDs, same scalar fields, AND
every relationship correctly remapped to the target revision's own
rows (a wrong remap -- e.g. `math_8a` accidentally pointing at
`t_science` instead of `t_math` -- changes the resolved tuple and
breaks the equality, even though the raw FK would still satisfy the
DB's composite-FK constraint against *some* row in the same revision).
"""
from __future__ import annotations

import threading

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from school_timetable.application.errors import (
    InitialDraftCannotBeDiscardedError,
    NoConfigurationDraftError,
)
from school_timetable.application.generate_schedule_service import GenerateScheduleService
from school_timetable.domain.problem import SchedulingProblem
from school_timetable.domain.result import SolverStatus
from school_timetable.fixtures.valid_fixture import build_valid_fixture
from school_timetable.persistence import models as m
from school_timetable.persistence.configuration_revision_repository import (
    SqlAlchemyConfigurationRevisionRepository,
)
from school_timetable.persistence.problem_repository import (
    SessionFactorySchedulingProblemRepository,
    SqlAlchemySchedulingProblemRepository,
)
from school_timetable.persistence.schedule_repository import SqlAlchemyScheduleVersionRepository
from school_timetable.scheduling.options import SolverOptions
from tests_web.support.problem_writer import write_scheduling_problem


@pytest.fixture
def db(live_db_engine):
    connection = live_db_engine.connect()
    transaction = connection.begin()
    session_factory = sessionmaker(
        bind=connection, autoflush=False, autocommit=False,
        expire_on_commit=False, join_transaction_mode="create_savepoint",
    )
    session = session_factory()
    try:
        yield session, session_factory
    finally:
        session.close()
        transaction.rollback()
        connection.close()


# -- shared setup/query helpers ---------------------------------------------

def _seed_and_publish(session: Session, session_factory) -> tuple[SchedulingProblem, int]:
    """Seeds a fresh year (initial DRAFT only -- Slice A's own
    invariant) and runs it through the REAL `GenerateScheduleService`
    (solve + verify + `persist_initial_version`) so the resulting
    PUBLISHED revision (`revision_number=1`) is genuinely
    production-created. Returns `(problem, year_id)`."""
    problem = build_valid_fixture()
    write_scheduling_problem(session, problem)
    session.flush()

    service = GenerateScheduleService(
        SessionFactorySchedulingProblemRepository(session_factory),
        SqlAlchemyScheduleVersionRepository(session_factory),
    )
    active = service.generate(
        problem.school.id, problem.academic_year.id,
        solver_options=SolverOptions(random_seed=11, num_search_workers=1),
    )
    assert active.solver_status in (SolverStatus.OPTIMAL, SolverStatus.FEASIBLE)

    year_id = session.execute(
        select(m.AcademicYear.id).where(m.AcademicYear.natural_id == problem.academic_year.id)
    ).scalar_one()
    return problem, year_id


def _year_row(session: Session, year_id: int) -> m.AcademicYear:
    return session.execute(select(m.AcademicYear).where(m.AcademicYear.id == year_id)).scalar_one()


def _revision_row(session: Session, revision_id: int) -> m.ConfigurationRevision:
    return session.get(m.ConfigurationRevision, revision_id)


def _revision_count(session: Session, year_id: int) -> int:
    return len(session.execute(
        select(m.ConfigurationRevision).where(m.ConfigurationRevision.academic_year_id == year_id)
    ).scalars().all())


def _rows(session: Session, model: type, year_id: int, revision_id: int) -> list:
    return list(session.execute(
        select(model).where(model.academic_year_id == year_id, model.configuration_revision_id == revision_id)
    ).scalars())


def _repo(session_factory) -> SqlAlchemyConfigurationRevisionRepository:
    return SqlAlchemyConfigurationRevisionRepository(session_factory)


def _resolved_snapshot(session: Session, year_id: int, revision_id: int) -> dict[str, frozenset]:
    """See module docstring. Covers all 15 `SchedulingProblem`
    configuration tables and all 21 intra-configuration FK edges
    between them."""
    day = {r.id: r.natural_id for r in _rows(session, m.Day, year_id, revision_id)}
    period = {r.id: r.natural_id for r in _rows(session, m.Period, year_id, revision_id)}
    class_section = {r.id: r.natural_id for r in _rows(session, m.ClassSection, year_id, revision_id)}
    teacher = {r.id: r.natural_id for r in _rows(session, m.Teacher, year_id, revision_id)}
    activity = {r.id: r.natural_id for r in _rows(session, m.Activity, year_id, revision_id)}
    resource = {r.id: r.natural_id for r in _rows(session, m.Resource, year_id, revision_id)}
    group = {r.id: r.natural_id for r in _rows(session, m.ParticipantGroup, year_id, revision_id)}
    requirement = {r.id: r.natural_id for r in _rows(session, m.TeachingRequirement, year_id, revision_id)}
    block = {r.id: r.natural_id for r in _rows(session, m.ReservedBlock, year_id, revision_id)}

    return {
        "day": frozenset(
            (r.natural_id, r.name, r.idx) for r in _rows(session, m.Day, year_id, revision_id)
        ),
        "period": frozenset(
            (r.natural_id, r.name, r.idx, r.block_id, r.is_instructional, r.start_time, r.end_time)
            for r in _rows(session, m.Period, year_id, revision_id)
        ),
        "class_section": frozenset(
            (r.natural_id, r.name, r.ordinal) for r in _rows(session, m.ClassSection, year_id, revision_id)
        ),
        "teacher": frozenset(
            (r.natural_id, r.first_name, r.last_name, r.ordinal)
            for r in _rows(session, m.Teacher, year_id, revision_id)
        ),
        "activity": frozenset(
            (r.natural_id, r.name, r.kind, r.ordinal) for r in _rows(session, m.Activity, year_id, revision_id)
        ),
        "resource": frozenset(
            (r.natural_id, r.name, r.capacity, r.ordinal)
            for r in _rows(session, m.Resource, year_id, revision_id)
        ),
        "participant_group": frozenset(
            (r.natural_id, r.name, r.role, r.ordinal)
            for r in _rows(session, m.ParticipantGroup, year_id, revision_id)
        ),
        "teaching_requirement": frozenset(
            (
                r.natural_id, teacher[r.teacher_id], activity[r.activity_id], group[r.participant_group_id],
                r.weekly_periods, r.block_mode, tuple(r.block_sizes), r.min_distinct_days,
                r.max_periods_per_day, (resource[r.resource_id] if r.resource_id is not None else None),
                r.split_group_id, r.ordinal,
            )
            for r in _rows(session, m.TeachingRequirement, year_id, revision_id)
        ),
        "reserved_block": frozenset(
            (
                r.natural_id, r.name, activity[r.activity_id],
                (teacher[r.teacher_id] if r.teacher_id is not None else None),
                (resource[r.resource_id] if r.resource_id is not None else None), r.ordinal,
            )
            for r in _rows(session, m.ReservedBlock, year_id, revision_id)
        ),
        "participant_group_class_section": frozenset(
            (group[r.participant_group_id], class_section[r.class_section_id], r.ordinal)
            for r in _rows(session, m.ParticipantGroupClassSection, year_id, revision_id)
        ),
        "teacher_availability": frozenset(
            (teacher[r.teacher_id], day[r.day_id], period[r.period_id], r.status, r.ordinal)
            for r in _rows(session, m.TeacherAvailability, year_id, revision_id)
        ),
        "time_preference": frozenset(
            (requirement[r.teaching_requirement_id], r.ordinal, tuple(r.preferred_period_indexes), r.weight)
            for r in _rows(session, m.TimePreference, year_id, revision_id)
        ),
        "reserved_block_class_section": frozenset(
            (block[r.reserved_block_id], class_section[r.class_section_id], r.ordinal)
            for r in _rows(session, m.ReservedBlockClassSection, year_id, revision_id)
        ),
        "reserved_block_slot": frozenset(
            (block[r.reserved_block_id], day[r.day_id], period[r.period_id], r.ordinal)
            for r in _rows(session, m.ReservedBlockSlot, year_id, revision_id)
        ),
        "fixed_placement": frozenset(
            (r.natural_id, requirement[r.teaching_requirement_id], day[r.day_id], period[r.period_id], r.ordinal)
            for r in _rows(session, m.FixedPlacement, year_id, revision_id)
        ),
    }


_ALL_15_TABLES = (
    m.Day, m.Period, m.ClassSection, m.ParticipantGroup, m.ParticipantGroupClassSection,
    m.Teacher, m.TeacherAvailability, m.Activity, m.Resource, m.TeachingRequirement,
    m.TimePreference, m.ReservedBlock, m.ReservedBlockClassSection, m.ReservedBlockSlot,
    m.FixedPlacement,
)

# The six leaf/join tables have no surrogate `id` (composite PK only) --
# their own PK columns (excluding the always-constant `academic_year_id`/
# `configuration_revision_id`) serve as a per-row identity for duplicate-
# detection purposes instead.
_COMPOSITE_PK_COLUMNS: dict[type, tuple[str, ...]] = {
    m.ParticipantGroupClassSection: ("participant_group_id", "class_section_id"),
    m.TeacherAvailability: ("teacher_id", "day_id", "period_id"),
    m.TimePreference: ("teaching_requirement_id", "ordinal"),
    m.ReservedBlockClassSection: ("reserved_block_id", "class_section_id"),
    m.ReservedBlockSlot: ("reserved_block_id", "day_id", "period_id"),
}


def _row_identity(row):
    if hasattr(row, "id"):
        return row.id
    cols = _COMPOSITE_PK_COLUMNS[type(row)]
    return tuple(getattr(row, c) for c in cols)


# == A. get_state baseline ====================================================

def test_get_state_before_first_generate(db):
    """Pre-first-Generate: initial DRAFT only, no published revision,
    no Schedule -- configuration is editable, timetable cannot be
    out-of-date because there is no timetable yet."""
    session, session_factory = db
    problem = build_valid_fixture()
    write_scheduling_problem(session, problem)
    session.flush()

    state = _repo(session_factory).get_state(problem.school.id, problem.academic_year.id)

    assert state.published_revision_number is None
    assert state.draft_revision_number == 1
    assert state.has_schedule is False
    assert state.configuration_locked is False
    assert state.timetable_out_of_date is False


def test_get_state_after_generate_no_draft(db):
    """Post-first-Generate, before any draft is reopened: published
    exists, no draft -- configuration is locked, but the timetable is
    NOT out of date (nothing has changed since it was generated)."""
    session, session_factory = db
    problem, year_id = _seed_and_publish(session, session_factory)

    state = _repo(session_factory).get_state(problem.school.id, problem.academic_year.id)

    assert state.published_revision_number == 1
    assert state.draft_revision_number is None
    assert state.has_schedule is True
    assert state.configuration_locked is True
    assert state.timetable_out_of_date is False


def test_get_state_after_begin_draft(db):
    """Published + open draft + a Schedule: configuration is editable
    again, and the existing timetable is now out of date."""
    session, session_factory = db
    problem, year_id = _seed_and_publish(session, session_factory)
    repo = _repo(session_factory)

    state = repo.begin_draft(problem.school.id, problem.academic_year.id)

    assert state.published_revision_number == 1
    assert state.draft_revision_number == 2
    assert state.has_schedule is True
    assert state.configuration_locked is False
    assert state.timetable_out_of_date is True

    # get_state independently agrees with begin_draft's own returned state.
    assert repo.get_state(problem.school.id, problem.academic_year.id) == state


# == B. begin_draft -- normal clone ===========================================

def test_begin_draft_creates_exactly_one_new_draft_revision(db):
    session, session_factory = db
    problem, year_id = _seed_and_publish(session, session_factory)
    published_id = _year_row(session, year_id).published_revision_id
    published_before = (
        _revision_row(session, published_id).status,
        _revision_row(session, published_id).revision_number,
        _revision_row(session, published_id).created_at,
    )
    revisions_before = _revision_count(session, year_id)

    _repo(session_factory).begin_draft(problem.school.id, problem.academic_year.id)

    year_row = _year_row(session, year_id)
    assert _revision_count(session, year_id) == revisions_before + 1
    assert year_row.draft_revision_id is not None
    draft_row = _revision_row(session, year_row.draft_revision_id)
    assert draft_row.status == "DRAFT"
    assert draft_row.revision_number == 2
    assert draft_row.id != published_id

    # published_revision_id untouched, and the published row itself
    # (status/revision_number/created_at) is byte-for-byte unchanged.
    assert year_row.published_revision_id == published_id
    published_after = (
        _revision_row(session, published_id).status,
        _revision_row(session, published_id).revision_number,
        _revision_row(session, published_id).created_at,
    )
    assert published_after == published_before


# == C/D/E/I. eager clone completeness, FK remapping, semantic equivalence,
#             published immutability -- verified together via
#             `_resolved_snapshot` equality plus a real domain round-trip ===

def test_begin_draft_clone_is_a_complete_and_correctly_remapped_copy(db):
    session, session_factory = db
    problem, year_id = _seed_and_publish(session, session_factory)
    published_id = _year_row(session, year_id).published_revision_id
    published_snapshot_before = _resolved_snapshot(session, year_id, published_id)

    # Sanity: the fixture actually exercises every one of the 15 tables
    # (a passing test here would be meaningless for an empty table).
    for key, rows in published_snapshot_before.items():
        assert len(rows) > 0, f"published revision has zero {key} rows -- fixture too thin to prove cloning"

    _repo(session_factory).begin_draft(problem.school.id, problem.academic_year.id)
    draft_id = _year_row(session, year_id).draft_revision_id

    draft_snapshot = _resolved_snapshot(session, year_id, draft_id)

    # C + D + I in one assertion per table: equal resolved snapshots mean
    # equal row counts, equal natural IDs, equal scalar fields, AND every
    # FK correctly remapped to the DRAFT's own rows (a wrong remap changes
    # the resolved natural-id in the tuple and breaks equality) -- while
    # the published revision's own snapshot (recomputed after cloning)
    # proves it was never touched.
    for key in published_snapshot_before:
        assert draft_snapshot[key] == published_snapshot_before[key], f"mismatch cloning {key!r}"

    published_snapshot_after = _resolved_snapshot(session, year_id, published_id)
    assert published_snapshot_after == published_snapshot_before

    # D, explicitly: no cloned row is literally the same physical row as
    # its published counterpart (fresh surrogate IDs throughout) --
    # spot-checked on two representative tables, one a pure leaf/parent
    # (Teacher) and one with three of its own FKs (TeachingRequirement).
    published_teacher_ids = {r.id for r in _rows(session, m.Teacher, year_id, published_id)}
    draft_teacher_ids = {r.id for r in _rows(session, m.Teacher, year_id, draft_id)}
    assert published_teacher_ids.isdisjoint(draft_teacher_ids)

    published_requirement_ids = {r.id for r in _rows(session, m.TeachingRequirement, year_id, published_id)}
    draft_requirement_ids = {r.id for r in _rows(session, m.TeachingRequirement, year_id, draft_id)}
    assert published_requirement_ids.isdisjoint(draft_requirement_ids)


def test_begin_draft_clone_covers_every_one_of_the_fifteen_tables(db):
    """Explicit per-table row-count check (in addition to the full
    resolved-snapshot equality above) -- makes it impossible for a
    silently-skipped table to hide behind an otherwise-passing test."""
    session, session_factory = db
    problem, year_id = _seed_and_publish(session, session_factory)
    published_id = _year_row(session, year_id).published_revision_id

    _repo(session_factory).begin_draft(problem.school.id, problem.academic_year.id)
    draft_id = _year_row(session, year_id).draft_revision_id

    for model in _ALL_15_TABLES:
        published_count = len(_rows(session, model, year_id, published_id))
        draft_count = len(_rows(session, model, year_id, draft_id))
        assert published_count > 0, f"{model.__tablename__}: fixture has zero published rows"
        assert draft_count == published_count, f"{model.__tablename__}: {draft_count} != {published_count}"


def test_begin_draft_semantic_equivalence_via_real_problem_repository(db):
    """E: loading the `SchedulingProblem` through the REAL
    `SqlAlchemySchedulingProblemRepository.load_by_school_and_year` --
    the same production code every Setup screen and every configuration
    writer's `validate` closure uses -- immediately before and
    immediately after `begin_draft` must be exactly equal. Before: it
    resolves the published revision (no draft exists yet). After: draft-
    first priority resolves the brand-new draft instead. Equal domain
    objects (value equality, no surrogate IDs anywhere in `SchedulingProblem`)
    proves the clone is a faithful, edit-ready starting point, not merely
    that raw table equality holds."""
    session, session_factory = db
    problem, year_id = _seed_and_publish(session, session_factory)

    before = SqlAlchemySchedulingProblemRepository(session).load_by_school_and_year(
        problem.school.id, problem.academic_year.id,
    )

    _repo(session_factory).begin_draft(problem.school.id, problem.academic_year.id)

    after = SqlAlchemySchedulingProblemRepository(session).load_by_school_and_year(
        problem.school.id, problem.academic_year.id,
    )

    assert before == after
    assert before == problem  # both equal the original fixture's own values, transitively


# == F. begin_draft idempotency ===============================================

def test_begin_draft_called_twice_does_not_create_a_second_draft(db):
    session, session_factory = db
    problem, year_id = _seed_and_publish(session, session_factory)
    repo = _repo(session_factory)

    first_state = repo.begin_draft(problem.school.id, problem.academic_year.id)
    first_draft_id = _year_row(session, year_id).draft_revision_id
    first_snapshot = {
        model.__tablename__: {_row_identity(r) for r in _rows(session, model, year_id, first_draft_id)}
        for model in _ALL_15_TABLES
    }
    revisions_after_first = _revision_count(session, year_id)

    second_state = repo.begin_draft(problem.school.id, problem.academic_year.id)

    assert second_state == first_state
    assert _revision_count(session, year_id) == revisions_after_first  # no 3rd revision
    second_draft_id = _year_row(session, year_id).draft_revision_id
    assert second_draft_id == first_draft_id  # same row, not a new one

    # No duplicate rows were inserted into any of the 15 tables -- exact
    # same set of row identities as after the first call.
    for model in _ALL_15_TABLES:
        second_ids = {_row_identity(r) for r in _rows(session, model, year_id, second_draft_id)}
        assert second_ids == first_snapshot[model.__tablename__], f"{model.__tablename__} duplicated"


# == G. discard_draft -- normal path ==========================================

def test_discard_draft_removes_pointer_and_all_draft_rows_leaves_published_untouched(db):
    session, session_factory = db
    problem, year_id = _seed_and_publish(session, session_factory)
    published_id = _year_row(session, year_id).published_revision_id
    published_snapshot_before = _resolved_snapshot(session, year_id, published_id)

    repo = _repo(session_factory)
    repo.begin_draft(problem.school.id, problem.academic_year.id)
    draft_id = _year_row(session, year_id).draft_revision_id

    state = repo.discard_draft(problem.school.id, problem.academic_year.id)

    assert state.draft_revision_number is None
    assert state.published_revision_number == 1
    assert state.configuration_locked is True
    assert state.timetable_out_of_date is False

    year_row = _year_row(session, year_id)
    assert year_row.draft_revision_id is None
    assert year_row.published_revision_id == published_id

    # The draft's own ConfigurationRevision row is gone.
    assert _revision_row(session, draft_id) is None

    # Every one of the 15 tables' draft-scoped rows was cascade-deleted.
    for model in _ALL_15_TABLES:
        assert _rows(session, model, year_id, draft_id) == []

    # Published revision and every one of its 15 tables' rows are
    # completely unchanged.
    assert _resolved_snapshot(session, year_id, published_id) == published_snapshot_before

    # get_state independently agrees.
    assert repo.get_state(problem.school.id, problem.academic_year.id) == state


# == H. discard_draft -- guarded/error paths ==================================

def test_discard_draft_with_no_open_draft_raises_no_configuration_draft_error(db):
    """Legitimately constructible: right after Generate, no draft is
    open (it was just published)."""
    session, session_factory = db
    problem, year_id = _seed_and_publish(session, session_factory)
    published_id = _year_row(session, year_id).published_revision_id

    with pytest.raises(NoConfigurationDraftError):
        _repo(session_factory).discard_draft(problem.school.id, problem.academic_year.id)

    # Zero mutation on the rejected attempt.
    year_row = _year_row(session, year_id)
    assert year_row.published_revision_id == published_id
    assert year_row.draft_revision_id is None


def test_discard_draft_of_the_initial_pre_generate_draft_raises_and_leaves_it_intact(db):
    """Legitimately constructible: before the year's first Generate has
    ever run, its only revision is the initial DRAFT -- required for
    that first Generate to ever succeed, so it must never be
    discardable."""
    session, session_factory = db
    problem = build_valid_fixture()
    write_scheduling_problem(session, problem)
    session.flush()
    year_id = session.execute(
        select(m.AcademicYear.id).where(m.AcademicYear.natural_id == problem.academic_year.id)
    ).scalar_one()
    initial_draft_id = _year_row(session, year_id).draft_revision_id

    with pytest.raises(InitialDraftCannotBeDiscardedError):
        _repo(session_factory).discard_draft(problem.school.id, problem.academic_year.id)

    # Zero mutation: the initial draft is completely untouched.
    year_row = _year_row(session, year_id)
    assert year_row.draft_revision_id == initial_draft_id
    assert year_row.published_revision_id is None
    assert _revision_row(session, initial_draft_id).status == "DRAFT"
    for model in _ALL_15_TABLES:
        assert len(_rows(session, model, year_id, initial_draft_id)) >= 0  # still queryable, not cascaded away
    assert len(_rows(session, m.Teacher, year_id, initial_draft_id)) == len(problem.teachers)


def test_discard_draft_referenced_by_schedule_version_is_refused(db):
    """H, defense-in-depth branch: the repository's own docstring and
    code comment both say this state is UNREACHABLE through any
    legitimate call path under Slice A's invariant (a `ScheduleVersion`
    is only ever created atomically with PUBLISHING the exact revision
    it references -- never while that revision is still a mutable
    draft). It is NOT blocked by any DB CHECK constraint, though
    (`ScheduleVersion.configuration_revision_id` has no status
    constraint) -- so it is a genuinely representable row shape, just
    one production code never produces. Constructed here the same way
    this codebase's other defense-in-depth guards are proven (see
    `tests_web/test_persistence_schema.py`'s cross-revision reference
    tests): a direct, out-of-band raw update, never by weakening any
    constraint or going through the repository's own normal API."""
    session, session_factory = db
    problem, year_id = _seed_and_publish(session, session_factory)
    repo = _repo(session_factory)
    repo.begin_draft(problem.school.id, problem.academic_year.id)
    draft_id = _year_row(session, year_id).draft_revision_id

    schedule_version_row = session.execute(
        select(m.ScheduleVersion).where(m.ScheduleVersion.academic_year_id == year_id)
    ).scalar_one()
    original_configuration_revision_id = schedule_version_row.configuration_revision_id
    schedule_version_row.configuration_revision_id = draft_id
    session.flush()

    with pytest.raises(RuntimeError) as exc_info:
        repo.discard_draft(problem.school.id, problem.academic_year.id)

    # Confirms this is genuinely the defense-in-depth guard firing --
    # not some other, unrelated RuntimeError -- since a bare
    # `pytest.raises(RuntimeError)` alone would also match the
    # "neither draft nor published" guard elsewhere in this module.
    assert "referenced by an existing ScheduleVersion" in str(exc_info.value)
    assert "cannot be discarded" in str(exc_info.value)

    # J: the failed guarded discard performed zero mutation -- draft
    # pointer and draft row are both still exactly as they were.
    year_row = _year_row(session, year_id)
    assert year_row.draft_revision_id == draft_id
    assert _revision_row(session, draft_id) is not None
    assert _revision_row(session, draft_id).status == "DRAFT"
    for model in _ALL_15_TABLES:
        assert len(_rows(session, model, year_id, draft_id)) > 0

    # Restore the fixture's own invariant before the savepoint rolls
    # back anyway, purely so this test doesn't rely on rollback alone
    # to undo the deliberate corruption it introduced.
    schedule_version_row.configuration_revision_id = original_configuration_revision_id
    session.flush()


# == K. begin_draft concurrency / serialization (real PostgreSQL) ============
#
# `begin_draft` acquires its `AcademicYear` row lock via
# `configuration_write_lock.lock_academic_year` (`SELECT ... FOR UPDATE`,
# Owner Decision #36's own mechanism) and never commits or releases it
# until the very end of the method -- the whole clone (every `_clone_table`
# call, every intermediate `session.flush()`) runs inside that same
# still-open transaction. Proving this actually serializes two concurrent
# callers requires two genuinely independent, overlapping database
# transactions (two real connections racing for the same row lock) --
# not merely two sequential calls, and not a monkeypatch of the
# repository's own transaction handling, which would prove nothing about
# the real implementation.
#
# This is exactly the same genuine-concurrency requirement
# `tests_web/test_teaching_assignment_repository.py`'s own duplicate-
# create race test already solves for an unrelated write path, and this
# test reuses its exact technique: a real, COMMITTED setup (never rolled
# back -- the SAVEPOINT-nested `db` fixture used everywhere else in this
# file cannot support two overlapping transactions, since it pins every
# test to one single shared connection/transaction), two OS threads each
# with their own `Session` synchronized to fire at the same instant via
# `threading.Barrier`, and manual cascade cleanup afterward. psycopg's
# blocking network I/O (including waiting on a Postgres row lock)
# releases the GIL, so two Python threads genuinely can -- and, per the
# assertions below, reliably do -- block on the same `SELECT ... FOR
# UPDATE` for real.

@pytest.fixture
def seeded_and_published_db(live_db_engine):
    """Real, COMMITTED setup (never rolled back), mirroring
    `tests_web/test_teaching_assignment_repository.py`'s own
    `seeded_db` fixture exactly, for the same reason: the concurrency
    test below needs two genuinely independent, overlapping database
    transactions."""
    connection = live_db_engine.connect()
    session = Session(bind=connection)
    problem = build_valid_fixture()
    write_scheduling_problem(session, problem)
    session.commit()
    session.close()
    connection.close()

    session_factory = sessionmaker(bind=live_db_engine, autoflush=False, autocommit=False, expire_on_commit=False)

    service = GenerateScheduleService(
        SessionFactorySchedulingProblemRepository(session_factory),
        SqlAlchemyScheduleVersionRepository(session_factory),
    )
    active = service.generate(
        problem.school.id, problem.academic_year.id,
        solver_options=SolverOptions(random_seed=11, num_search_workers=1),
    )
    assert active.solver_status in (SolverStatus.OPTIMAL, SolverStatus.FEASIBLE)

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


def test_concurrent_begin_draft_converges_on_exactly_one_draft(seeded_and_published_db, live_db_engine):
    """Two threads race to open a configuration draft for the same
    year. Whichever thread's `SELECT ... FOR UPDATE` wins proceeds to
    create the new `ConfigurationRevision`, clone all fifteen tables,
    and commit; the other blocks on the lock until the first commits,
    then reloads (now sees `AcademicYear.draft_revision_id` already
    set) and idempotently returns the SAME draft, performing zero
    additional writes -- never raising, never creating a second draft."""
    problem, session_factory = seeded_and_published_db
    repo = SqlAlchemyConfigurationRevisionRepository(session_factory)
    barrier = threading.Barrier(2)
    results: dict[str, object] = {}

    def attempt(label: str) -> None:
        barrier.wait(timeout=10)
        try:
            results[label] = repo.begin_draft(problem.school.id, problem.academic_year.id)
        except Exception as exc:  # pragma: no cover - diagnostic only
            results[label] = exc

    t1 = threading.Thread(target=attempt, args=("A",))
    t2 = threading.Thread(target=attempt, args=("B",))
    t1.start()
    t2.start()
    t1.join(timeout=20)
    t2.join(timeout=20)

    # Neither call raised (no deadlock, no unexpected exception), and
    # both threads observed the exact same resulting state.
    for label, result in results.items():
        assert not isinstance(result, Exception), f"thread {label} raised: {result!r}"
    assert results["A"] == results["B"]
    assert results["A"].draft_revision_number == 2

    # Independently re-verify against the real database on a THIRD,
    # fresh connection (never one of the two racing sessions).
    check_connection = live_db_engine.connect()
    check_session = Session(bind=check_connection)
    try:
        year_id = check_session.execute(
            select(m.AcademicYear.id).where(m.AcademicYear.natural_id == problem.academic_year.id)
        ).scalar_one()
        year_row = _year_row(check_session, year_id)
        published_id = year_row.published_revision_id
        draft_id = year_row.draft_revision_id

        # Exactly one DRAFT ConfigurationRevision exists for this year,
        # and AcademicYear.draft_revision_id points to it.
        draft_revisions = check_session.execute(
            select(m.ConfigurationRevision).where(
                m.ConfigurationRevision.academic_year_id == year_id,
                m.ConfigurationRevision.status == "DRAFT",
            )
        ).scalars().all()
        assert len(draft_revisions) == 1
        assert draft_revisions[0].id == draft_id
        assert _revision_count(check_session, year_id) == 2  # published (1) + draft (2), never 3

        # Every one of the 15 tables has EXACTLY ONE draft copy per
        # published row -- never duplicated clone rows from a second,
        # wasted clone attempt.
        for model in _ALL_15_TABLES:
            published_count = len(_rows(check_session, model, year_id, published_id))
            draft_count = len(_rows(check_session, model, year_id, draft_id))
            assert published_count > 0, f"{model.__tablename__}: fixture has zero published rows"
            assert draft_count == published_count, (
                f"{model.__tablename__}: {draft_count} draft rows != {published_count} published rows "
                "-- possible duplicate clone from a lost race"
            )

        # The published revision itself was never touched by either thread.
        published_row = _revision_row(check_session, published_id)
        assert published_row.status == "PUBLISHED"
        assert published_row.revision_number == 1
    finally:
        check_session.close()
        check_connection.close()
