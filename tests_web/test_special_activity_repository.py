"""Real-PostgreSQL integration + concurrency tests for Reserved
Activities Slice A1's Special Activity write path:
`persistence.special_activity_repository.SqlAlchemySpecialActivityRepository`
and the Owner-Decision-#36 generation-vs-write race closure, now proven
for a Special Activity write too (already proven for Teacher/Class/
Teaching Assignment/Subject writes in the sibling `test_*_repository.py`
files).

Also proves the corrected derived-name product contract: renaming a
Special Activity atomically synchronizes every `ReservedBlock.name`
referencing it, in the same transaction as the Activity rename.

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
    SpecialActivityInUseError,
    SpecialActivityNotFoundError,
)
from school_timetable.application.special_activity_rules import validate_create, validate_delete, validate_update
from school_timetable.domain.result import SolverStatus
from school_timetable.fixtures.valid_fixture import build_valid_fixture
from school_timetable.persistence import models as m
from school_timetable.persistence.problem_repository import SessionFactorySchedulingProblemRepository
from school_timetable.persistence.schedule_repository import SqlAlchemyScheduleVersionRepository
from school_timetable.persistence.special_activity_repository import SqlAlchemySpecialActivityRepository
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


def _special_activity_ids(session_factory, school_id, year_id):
    problem = SessionFactorySchedulingProblemRepository(session_factory).load_by_school_and_year(school_id, year_id)
    return {a.id for a in problem.activities if a.kind.value == "CLUB"}


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


def _reserved_block_names(session_factory, year_id_surrogate):
    session = session_factory()
    try:
        rows = session.execute(
            select(m.ReservedBlock.natural_id, m.ReservedBlock.name).where(
                m.ReservedBlock.academic_year_id == year_id_surrogate,
            )
        ).all()
        return dict(rows)
    finally:
        session.close()


# -- A. write persistence -----------------------------------------------

def test_create_round_trip(seeded_db):
    problem, session_factory = seeded_db
    repo = SqlAlchemySpecialActivityRepository(session_factory)

    def validate(current_problem):
        validate_create(current_problem, problem.school.id, problem.academic_year.id, name="Debate Club")

    repo.create(
        problem.school.id, problem.academic_year.id, "activity_test_create", "Debate Club", validate=validate,
    )

    reloaded = SessionFactorySchedulingProblemRepository(session_factory).load_by_school_and_year(
        problem.school.id, problem.academic_year.id,
    )
    created = next(a for a in reloaded.activities if a.id == "activity_test_create")
    assert created.name == "Debate Club"
    assert created.kind.value == "CLUB"


def test_create_next_ordinal_is_max_across_all_activities_plus_one(seeded_db, live_db_engine):
    problem, session_factory = seeded_db
    repo = SqlAlchemySpecialActivityRepository(session_factory)

    def validate(current_problem):
        validate_create(current_problem, problem.school.id, problem.academic_year.id, name="Debate Club")

    repo.create(
        problem.school.id, problem.academic_year.id, "activity_test_ordinal", "Debate Club", validate=validate,
    )

    year_id = _year_id(session_factory, problem.school.id, problem.academic_year.id)
    connection = live_db_engine.connect()
    session = Session(bind=connection)
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
    repo = SqlAlchemySpecialActivityRepository(session_factory)
    before_ids = _special_activity_ids(session_factory, problem.school.id, problem.academic_year.id)

    def failing_validate(current_problem):
        raise SpecialActivityNotFoundError(problem.school.id, problem.academic_year.id, "irrelevant")

    with pytest.raises(SpecialActivityNotFoundError):
        repo.create(
            problem.school.id, problem.academic_year.id, "activity_should_not_exist",
            "Debate Club", validate=failing_validate,
        )

    after_ids = _special_activity_ids(session_factory, problem.school.id, problem.academic_year.id)
    assert after_ids == before_ids
    assert "activity_should_not_exist" not in after_ids


def test_update_round_trip(seeded_db):
    problem, session_factory = seeded_db
    repo = SqlAlchemySpecialActivityRepository(session_factory)

    def validate(current_problem):
        validate_update(
            current_problem, problem.school.id, problem.academic_year.id, "club_chess",
            name="Strategy Games Club",
        )

    repo.update(problem.school.id, problem.academic_year.id, "club_chess", "Strategy Games Club", validate=validate)

    reloaded = SessionFactorySchedulingProblemRepository(session_factory).load_by_school_and_year(
        problem.school.id, problem.academic_year.id,
    )
    updated = next(a for a in reloaded.activities if a.id == "club_chess")
    assert updated.name == "Strategy Games Club"
    assert updated.kind.value == "CLUB"


def test_update_preserves_ordinal_and_ids(seeded_db, live_db_engine):
    problem, session_factory = seeded_db
    repo = SqlAlchemySpecialActivityRepository(session_factory)
    year_id = _year_id(session_factory, problem.school.id, problem.academic_year.id)

    connection = live_db_engine.connect()
    session = Session(bind=connection)
    before = session.execute(
        select(m.Activity).where(m.Activity.academic_year_id == year_id, m.Activity.natural_id == "club_chess")
    ).scalar_one()
    id_before, ordinal_before = before.id, before.ordinal
    session.close()
    connection.close()

    def validate(current_problem):
        validate_update(current_problem, problem.school.id, problem.academic_year.id, "club_chess", name="Renamed")

    repo.update(problem.school.id, problem.academic_year.id, "club_chess", "Renamed", validate=validate)

    connection2 = live_db_engine.connect()
    session2 = Session(bind=connection2)
    after = session2.execute(
        select(m.Activity).where(m.Activity.academic_year_id == year_id, m.Activity.natural_id == "club_chess")
    ).scalar_one()
    session2.close()
    connection2.close()

    assert after.id == id_before
    assert after.ordinal == ordinal_before
    assert after.kind == "CLUB"


def test_update_validation_failure_leaves_name_unchanged(seeded_db):
    """Atomicity proof B: update validation failure -- name unchanged."""
    problem, session_factory = seeded_db
    repo = SqlAlchemySpecialActivityRepository(session_factory)

    def failing_validate(current_problem):
        raise SpecialActivityNotFoundError(problem.school.id, problem.academic_year.id, "club_chess")

    with pytest.raises(SpecialActivityNotFoundError):
        repo.update(problem.school.id, problem.academic_year.id, "club_chess", "Renamed", validate=failing_validate)

    reloaded = SessionFactorySchedulingProblemRepository(session_factory).load_by_school_and_year(
        problem.school.id, problem.academic_year.id,
    )
    unchanged = next(a for a in reloaded.activities if a.id == "club_chess")
    assert unchanged.name == "Chess Club"


def test_update_ordinary_row_via_repository_raises_special_activity_not_found(seeded_db):
    """Even if a caller's `validate` callback were somehow bypassed
    (defense-in-depth), the repository itself authoritatively
    re-checks `row.kind == 'CLUB'` against the resolved ORM row."""
    problem, session_factory = seeded_db
    repo = SqlAlchemySpecialActivityRepository(session_factory)

    def permissive_validate(current_problem):
        pass  # Deliberately does not check kind, to prove the repository's own guard.

    with pytest.raises(SpecialActivityNotFoundError):
        repo.update(problem.school.id, problem.academic_year.id, "math", "Renamed", validate=permissive_validate)

    reloaded = SessionFactorySchedulingProblemRepository(session_factory).load_by_school_and_year(
        problem.school.id, problem.academic_year.id,
    )
    subject = next(a for a in reloaded.activities if a.id == "math")
    assert subject.name == "Mathematics"
    assert subject.kind.value == "ORDINARY"


def test_delete_round_trip(seeded_db):
    problem, session_factory = seeded_db
    repo = SqlAlchemySpecialActivityRepository(session_factory)

    def validate_c(current_problem):
        validate_create(current_problem, problem.school.id, problem.academic_year.id, name="Unused")

    repo.create(problem.school.id, problem.academic_year.id, "activity_to_delete", "Unused", validate=validate_c)

    def validate_d(current_problem):
        validate_delete(current_problem, problem.school.id, problem.academic_year.id, "activity_to_delete")

    repo.delete(problem.school.id, problem.academic_year.id, "activity_to_delete", validate=validate_d)

    remaining_ids = _special_activity_ids(session_factory, problem.school.id, problem.academic_year.id)
    assert "activity_to_delete" not in remaining_ids


def test_delete_blocker_leaves_nothing_deleted(seeded_db):
    """Atomicity proof C: delete blocked -- Activity remains, reference
    remains."""
    problem, session_factory = seeded_db
    repo = SqlAlchemySpecialActivityRepository(session_factory)

    def validate(current_problem):
        validate_delete(current_problem, problem.school.id, problem.academic_year.id, "club_chess")

    with pytest.raises(SpecialActivityInUseError):
        repo.delete(problem.school.id, problem.academic_year.id, "club_chess", validate=validate)

    remaining_ids = _special_activity_ids(session_factory, problem.school.id, problem.academic_year.id)
    assert "club_chess" in remaining_ids


def test_delete_ordinary_row_via_repository_raises_special_activity_not_found(seeded_db):
    """Atomicity proof D: an ORDINARY target -- Subject row unchanged."""
    problem, session_factory = seeded_db
    repo = SqlAlchemySpecialActivityRepository(session_factory)

    def permissive_validate(current_problem):
        pass

    with pytest.raises(SpecialActivityNotFoundError):
        repo.delete(problem.school.id, problem.academic_year.id, "math", validate=permissive_validate)

    reloaded = SessionFactorySchedulingProblemRepository(session_factory).load_by_school_and_year(
        problem.school.id, problem.academic_year.id,
    )
    subject = next(a for a in reloaded.activities if a.id == "math")
    assert subject.name == "Mathematics"


# -- B. rename derived-name synchronization -------------------------------

def test_rename_synchronizes_every_referencing_reserved_block_name(seeded_db, live_db_engine):
    """The mandatory corrected-contract invariant: renaming
    `club_chess` (referenced by exactly one ReservedBlock, `club_chess`,
    in the fixture) atomically updates that block's own `name` column
    to match. `club_robotics`'s own unrelated ReservedBlock is never
    touched."""
    problem, session_factory = seeded_db
    repo = SqlAlchemySpecialActivityRepository(session_factory)
    year_id = _year_id(session_factory, problem.school.id, problem.academic_year.id)

    before = _reserved_block_names(session_factory, year_id)
    assert before["club_chess"] == "Chess Club"
    assert before["club_robotics"] == "Robotics Club"

    def validate(current_problem):
        validate_update(current_problem, problem.school.id, problem.academic_year.id, "club_chess", name="STEM Lab")

    repo.update(problem.school.id, problem.academic_year.id, "club_chess", "STEM Lab", validate=validate)

    after = _reserved_block_names(session_factory, year_id)
    assert after["club_chess"] == "STEM Lab"
    assert after["club_robotics"] == "Robotics Club"


def test_rename_synchronizes_multiple_blocks_referencing_the_same_activity(seeded_db, live_db_engine):
    """A second `ReservedBlock` targeting the same renamed Activity is
    also synchronized; a third block targeting a different Activity is
    left untouched."""
    problem, session_factory = seeded_db
    repo = SqlAlchemySpecialActivityRepository(session_factory)
    year_id = _year_id(session_factory, problem.school.id, problem.academic_year.id)

    # Insert a second ReservedBlock referencing club_chess directly
    # (real committed transaction, exactly like the fixture seed).
    connection = live_db_engine.connect()
    session = Session(bind=connection)
    activity_row = session.execute(
        select(m.Activity).where(m.Activity.academic_year_id == year_id, m.Activity.natural_id == "club_chess")
    ).scalar_one()
    day_row = session.execute(
        select(m.Day).where(m.Day.academic_year_id == year_id, m.Day.natural_id == "mon")
    ).scalar_one()
    period_row = session.execute(
        select(m.Period).where(m.Period.academic_year_id == year_id, m.Period.natural_id == "p1")
    ).scalar_one()
    class_row = session.execute(
        select(m.ClassSection).where(
            m.ClassSection.academic_year_id == year_id, m.ClassSection.natural_id == "9a",
        )
    ).scalar_one()
    revision_id = session.get(m.AcademicYear, year_id).draft_revision_id
    extra_block = m.ReservedBlock(
        academic_year_id=year_id, configuration_revision_id=revision_id,
        natural_id="club_chess_extra", name="Chess Club", activity_id=activity_row.id,
        ordinal=999,
    )
    session.add(extra_block)
    session.flush()
    session.add(m.ReservedBlockClassSection(
        academic_year_id=year_id, configuration_revision_id=revision_id,
        reserved_block_id=extra_block.id, class_section_id=class_row.id, ordinal=0,
    ))
    session.add(m.ReservedBlockSlot(
        academic_year_id=year_id, configuration_revision_id=revision_id,
        reserved_block_id=extra_block.id, day_id=day_row.id, period_id=period_row.id,
        ordinal=0,
    ))
    session.commit()
    session.close()
    connection.close()

    def validate(current_problem):
        validate_update(current_problem, problem.school.id, problem.academic_year.id, "club_chess", name="STEM Lab")

    repo.update(problem.school.id, problem.academic_year.id, "club_chess", "STEM Lab", validate=validate)

    after = _reserved_block_names(session_factory, year_id)
    assert after["club_chess"] == "STEM Lab"
    assert after["club_chess_extra"] == "STEM Lab"
    assert after["club_robotics"] == "Robotics Club"


def test_rename_validation_failure_synchronizes_nothing(seeded_db):
    """Atomicity proof E: a failing rename must leave BOTH the Activity
    name AND every dependent ReservedBlock.name completely untouched --
    zero partial synchronization."""
    problem, session_factory = seeded_db
    repo = SqlAlchemySpecialActivityRepository(session_factory)
    year_id = _year_id(session_factory, problem.school.id, problem.academic_year.id)

    def failing_validate(current_problem):
        raise SpecialActivityNotFoundError(problem.school.id, problem.academic_year.id, "club_chess")

    with pytest.raises(SpecialActivityNotFoundError):
        repo.update(problem.school.id, problem.academic_year.id, "club_chess", "STEM Lab", validate=failing_validate)

    reloaded = SessionFactorySchedulingProblemRepository(session_factory).load_by_school_and_year(
        problem.school.id, problem.academic_year.id,
    )
    assert next(a for a in reloaded.activities if a.id == "club_chess").name == "Chess Club"
    after = _reserved_block_names(session_factory, year_id)
    assert after["club_chess"] == "Chess Club"


# -- C. generation-vs-write race (Owner Decision #36) --------------------

def test_generation_persist_aborts_when_special_activity_write_committed_since_load(seeded_db, live_db_engine):
    """Sequential, fully deterministic simulation of: (1) generation
    loads problem P; (2) a Special Activity write (a rename, which also
    synchronizes its ReservedBlock.name) commits; (3) generation
    attempts to persist against the now-stale P. Mirrors
    `test_subject_repository.py`'s identical proof, now for a Special
    Activity mutation -- `SchedulingProblem.activities` AND
    `.reserved_blocks` are both part of the frozen-dataclass equality
    the generation persist step compares, so the rename's dependent
    block-name sync is independently sufficient to trigger this even if
    only `activities` had changed."""
    problem, session_factory = seeded_db
    schedule_repo = SqlAlchemyScheduleVersionRepository(session_factory)
    special_activity_repo = SqlAlchemySpecialActivityRepository(session_factory)

    stale_problem = SessionFactorySchedulingProblemRepository(session_factory).load_by_school_and_year(
        problem.school.id, problem.academic_year.id,
    )

    def validate(current_problem):
        validate_update(current_problem, problem.school.id, problem.academic_year.id, "club_chess", name="STEM Lab")

    special_activity_repo.update(
        problem.school.id, problem.academic_year.id, "club_chess", "STEM Lab", validate=validate,
    )

    with pytest.raises(ConfigurationChangedDuringGenerationError):
        schedule_repo.persist_initial_version(
            problem.school.id, problem.academic_year.id, stale_problem,
            entries=(), solver_status=SolverStatus.OPTIMAL, total_soft_penalty=0,
            wall_time_seconds=0.01, random_seed=None,
        )

    # No Schedule/ScheduleVersion/ScheduleEntry row was ever created.
    year_id = _year_id(session_factory, problem.school.id, problem.academic_year.id)
    connection = live_db_engine.connect()
    session = Session(bind=connection)
    schedule_count = len(
        session.execute(select(m.Schedule).where(m.Schedule.academic_year_id == year_id)).scalars().all()
    )
    session.close()
    connection.close()
    assert schedule_count == 0


def test_generation_persist_succeeds_then_blocks_a_waiting_special_activity_write(seeded_db):
    """Opposite ordering: generation reaches the final locked persist
    first and commits; a Special Activity write attempted afterward
    must see the now-generated schedule and be rejected under Decision
    #35, with the configuration (including every ReservedBlock.name)
    left unchanged."""
    problem, session_factory = seeded_db
    schedule_repo = SqlAlchemyScheduleVersionRepository(session_factory)
    special_activity_repo = SqlAlchemySpecialActivityRepository(session_factory)
    year_id = _year_id(session_factory, problem.school.id, problem.academic_year.id)

    loaded_problem = SessionFactorySchedulingProblemRepository(session_factory).load_by_school_and_year(
        problem.school.id, problem.academic_year.id,
    )

    schedule_repo.persist_initial_version(
        problem.school.id, problem.academic_year.id, loaded_problem,
        entries=(), solver_status=SolverStatus.OPTIMAL, total_soft_penalty=0,
        wall_time_seconds=0.01, random_seed=None,
    )

    def validate(current_problem):
        validate_update(current_problem, problem.school.id, problem.academic_year.id, "club_chess", name="STEM Lab")

    with pytest.raises(ConfigurationLockedError):
        special_activity_repo.update(
            problem.school.id, problem.academic_year.id, "club_chess", "STEM Lab", validate=validate,
        )

    reloaded = SessionFactorySchedulingProblemRepository(session_factory).load_by_school_and_year(
        problem.school.id, problem.academic_year.id,
    )
    assert next(a for a in reloaded.activities if a.id == "club_chess").name == "Chess Club"
    after = _reserved_block_names(session_factory, year_id)
    assert after["club_chess"] == "Chess Club"
