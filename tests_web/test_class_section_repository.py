"""Real-PostgreSQL integration + concurrency tests for Real-School
Setup MVP Slice C's Class write path:
`persistence.class_section_repository.SqlAlchemyClassSectionRepository`
and the Owner-Decision-#36 generation-vs-write race closure, now proven
for a Class write too (already proven for Teaching Assignment and
Teacher writes in the sibling `test_*_repository.py` files).

Everything here is seeded with a real, committed transaction (like
`test_teacher_repository.py`, for the same reason: some tests need two
independent, overlapping database transactions), and the `seeded_db`
fixture manually deletes every row it created afterward (cascading from
the one `School` row) regardless of test outcome.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from school_timetable.application.class_section_rules import (
    CanonicalWholeClassGroupInvariantError,
    validate_create,
    validate_delete,
    validate_update,
)
from school_timetable.application.errors import (
    ClassSectionInUseError,
    ClassSectionNotFoundError,
    ConfigurationChangedDuringGenerationError,
    ConfigurationLockedError,
)
from school_timetable.domain.result import SolverStatus
from school_timetable.fixtures.valid_fixture import build_valid_fixture
from school_timetable.persistence import models as m
from school_timetable.persistence.class_section_repository import SqlAlchemyClassSectionRepository
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


def _class_ids(session_factory, school_id, year_id):
    problem = SessionFactorySchedulingProblemRepository(session_factory).load_by_school_and_year(school_id, year_id)
    return {c.id for c in problem.class_sections}


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
    repo = SqlAlchemyClassSectionRepository(session_factory)

    def validate(current_problem):
        validate_create(current_problem, problem.school.id, problem.academic_year.id, name="10-A")

    repo.create(
        problem.school.id, problem.academic_year.id, "class_test_create", "group_test_create",
        "10-A", validate=validate,
    )

    reloaded = SessionFactorySchedulingProblemRepository(session_factory).load_by_school_and_year(
        problem.school.id, problem.academic_year.id,
    )
    created_class = next(c for c in reloaded.class_sections if c.id == "class_test_create")
    assert created_class.name == "10-A"
    created_group = next(g for g in reloaded.participant_groups if g.id == "group_test_create")
    assert created_group.name == "10-A"
    assert created_group.role.value == "WHOLE_CLASS"
    assert created_group.class_sections == ("class_test_create",)


def test_create_next_ordinal_is_max_plus_one(seeded_db, live_db_engine):
    problem, session_factory = seeded_db
    repo = SqlAlchemyClassSectionRepository(session_factory)

    def validate(current_problem):
        validate_create(current_problem, problem.school.id, problem.academic_year.id, name="10-A")

    repo.create(
        problem.school.id, problem.academic_year.id, "class_test_ordinal", "group_test_ordinal",
        "10-A", validate=validate,
    )

    connection = live_db_engine.connect()
    session = Session(bind=connection)
    year_id = session.execute(
        select(m.AcademicYear.id).join(m.School, m.School.id == m.AcademicYear.school_id).where(
            m.School.natural_id == problem.school.id, m.AcademicYear.natural_id == problem.academic_year.id,
        )
    ).scalar_one()
    class_ordinals = session.execute(
        select(m.ClassSection.ordinal).where(m.ClassSection.academic_year_id == year_id)
        .order_by(m.ClassSection.ordinal)
    ).scalars().all()
    group_ordinals = session.execute(
        select(m.ParticipantGroup.ordinal).where(m.ParticipantGroup.academic_year_id == year_id)
        .order_by(m.ParticipantGroup.ordinal)
    ).scalars().all()
    session.close()
    connection.close()
    assert class_ordinals == list(range(len(problem.class_sections) + 1))
    assert group_ordinals == list(range(len(problem.participant_groups) + 1))


def test_create_validation_failure_leaves_no_partial_mutation(seeded_db):
    """Atomicity proof A: create validation failure -- no ClassSection,
    no ParticipantGroup, no membership committed."""
    problem, session_factory = seeded_db
    repo = SqlAlchemyClassSectionRepository(session_factory)
    before_ids = _class_ids(session_factory, problem.school.id, problem.academic_year.id)

    def failing_validate(current_problem):
        raise ClassSectionNotFoundError(problem.school.id, problem.academic_year.id, "irrelevant")

    with pytest.raises(ClassSectionNotFoundError):
        repo.create(
            problem.school.id, problem.academic_year.id, "class_should_not_exist", "group_should_not_exist",
            "10-A", validate=failing_validate,
        )

    after_ids = _class_ids(session_factory, problem.school.id, problem.academic_year.id)
    assert after_ids == before_ids

    year_id = _year_id(session_factory, problem.school.id, problem.academic_year.id)
    session = session_factory()
    group_row = session.execute(
        select(m.ParticipantGroup).where(
            m.ParticipantGroup.academic_year_id == year_id, m.ParticipantGroup.natural_id == "group_should_not_exist",
        )
    ).scalar_one_or_none()
    session.close()
    assert group_row is None


def test_update_round_trip(seeded_db):
    problem, session_factory = seeded_db
    repo = SqlAlchemyClassSectionRepository(session_factory)

    def validate(current_problem):
        validate_update(current_problem, problem.school.id, problem.academic_year.id, "8a", name="8-Z")

    repo.update(problem.school.id, problem.academic_year.id, "8a", "8-Z", validate=validate)

    reloaded = SessionFactorySchedulingProblemRepository(session_factory).load_by_school_and_year(
        problem.school.id, problem.academic_year.id,
    )
    updated_class = next(c for c in reloaded.class_sections if c.id == "8a")
    assert updated_class.name == "8-Z"
    canonical_group = next(
        g for g in reloaded.participant_groups if g.role.value == "WHOLE_CLASS" and g.class_sections == ("8a",)
    )
    assert canonical_group.name == "8-Z"
    assert canonical_group.id == "pg_8a"  # canonical group natural ID preserved


def test_update_preserves_ordinals_and_ids(seeded_db, live_db_engine):
    problem, session_factory = seeded_db
    repo = SqlAlchemyClassSectionRepository(session_factory)

    connection = live_db_engine.connect()
    session = Session(bind=connection)
    year_id = session.execute(
        select(m.AcademicYear.id).join(m.School, m.School.id == m.AcademicYear.school_id).where(
            m.School.natural_id == problem.school.id, m.AcademicYear.natural_id == problem.academic_year.id,
        )
    ).scalar_one()
    class_before = session.execute(
        select(m.ClassSection).where(m.ClassSection.academic_year_id == year_id, m.ClassSection.natural_id == "8a")
    ).scalar_one()
    class_id_before, class_ordinal_before = class_before.id, class_before.ordinal
    group_before = session.execute(
        select(m.ParticipantGroup).where(
            m.ParticipantGroup.academic_year_id == year_id, m.ParticipantGroup.natural_id == "pg_8a",
        )
    ).scalar_one()
    group_id_before, group_ordinal_before = group_before.id, group_before.ordinal
    session.close()
    connection.close()

    def validate(current_problem):
        validate_update(current_problem, problem.school.id, problem.academic_year.id, "8a", name="8-Z")

    repo.update(problem.school.id, problem.academic_year.id, "8a", "8-Z", validate=validate)

    connection2 = live_db_engine.connect()
    session2 = Session(bind=connection2)
    class_after = session2.execute(
        select(m.ClassSection).where(m.ClassSection.academic_year_id == year_id, m.ClassSection.natural_id == "8a")
    ).scalar_one()
    group_after = session2.execute(
        select(m.ParticipantGroup).where(
            m.ParticipantGroup.academic_year_id == year_id, m.ParticipantGroup.natural_id == "pg_8a",
        )
    ).scalar_one()
    session2.close()
    connection2.close()

    assert class_after.id == class_id_before
    assert class_after.ordinal == class_ordinal_before
    assert group_after.id == group_id_before
    assert group_after.ordinal == group_ordinal_before


def test_update_validation_failure_changes_neither_name(seeded_db):
    """Atomicity proof B: update validation failure -- neither the
    class name nor the canonical group name changes."""
    problem, session_factory = seeded_db
    repo = SqlAlchemyClassSectionRepository(session_factory)

    def failing_validate(current_problem):
        raise ClassSectionNotFoundError(problem.school.id, problem.academic_year.id, "8a")

    with pytest.raises(ClassSectionNotFoundError):
        repo.update(problem.school.id, problem.academic_year.id, "8a", "8-Z", validate=failing_validate)

    reloaded = SessionFactorySchedulingProblemRepository(session_factory).load_by_school_and_year(
        problem.school.id, problem.academic_year.id,
    )
    unchanged_class = next(c for c in reloaded.class_sections if c.id == "8a")
    assert unchanged_class.name == "8-A"
    canonical_group = next(
        g for g in reloaded.participant_groups if g.role.value == "WHOLE_CLASS" and g.class_sections == ("8a",)
    )
    assert canonical_group.name == "All of 8-A"


def test_delete_round_trip(seeded_db):
    problem, session_factory = seeded_db
    repo = SqlAlchemyClassSectionRepository(session_factory)

    def validate_c(current_problem):
        validate_create(current_problem, problem.school.id, problem.academic_year.id, name="Unused")

    repo.create(
        problem.school.id, problem.academic_year.id, "class_to_delete", "group_to_delete",
        "Unused", validate=validate_c,
    )

    def validate_d(current_problem):
        validate_delete(current_problem, problem.school.id, problem.academic_year.id, "class_to_delete")

    repo.delete(problem.school.id, problem.academic_year.id, "class_to_delete", validate=validate_d)

    remaining_ids = _class_ids(session_factory, problem.school.id, problem.academic_year.id)
    assert "class_to_delete" not in remaining_ids

    year_id = _year_id(session_factory, problem.school.id, problem.academic_year.id)
    session = session_factory()
    group_row = session.execute(
        select(m.ParticipantGroup).where(
            m.ParticipantGroup.academic_year_id == year_id, m.ParticipantGroup.natural_id == "group_to_delete",
        )
    ).scalar_one_or_none()
    session.close()
    assert group_row is None


def test_delete_blocker_leaves_nothing_deleted(seeded_db):
    """Atomicity proof C: delete blocker -- nothing deleted."""
    problem, session_factory = seeded_db
    repo = SqlAlchemyClassSectionRepository(session_factory)

    def validate(current_problem):
        validate_delete(current_problem, problem.school.id, problem.academic_year.id, "9a")

    with pytest.raises(ClassSectionInUseError):
        repo.delete(problem.school.id, problem.academic_year.id, "9a", validate=validate)

    remaining_ids = _class_ids(session_factory, problem.school.id, problem.academic_year.id)
    assert "9a" in remaining_ids

    reloaded = SessionFactorySchedulingProblemRepository(session_factory).load_by_school_and_year(
        problem.school.id, problem.academic_year.id,
    )
    canonical_group = next(
        g for g in reloaded.participant_groups if g.role.value == "WHOLE_CLASS" and g.class_sections == ("9a",)
    )
    assert canonical_group.id == "pg_9a"  # canonical group untouched


def test_unexpected_canonical_invariant_defect_leaves_no_partial_mutation(seeded_db, live_db_engine):
    """Atomicity proof D: an unexpected canonical invariant defect --
    nothing partially mutated. Simulated by creating a fresh, unused
    class (so its canonical group carries no other reference) and then
    deleting only its canonical group + membership directly (outside
    the normal write path), leaving the ClassSection row orphaned,
    before attempting an update through the repository."""
    problem, session_factory = seeded_db
    repo = SqlAlchemyClassSectionRepository(session_factory)

    def validate_c(current_problem):
        validate_create(current_problem, problem.school.id, problem.academic_year.id, name="Orphan")

    repo.create(
        problem.school.id, problem.academic_year.id, "class_orphan", "group_orphan",
        "Orphan", validate=validate_c,
    )

    connection = live_db_engine.connect()
    session = Session(bind=connection)
    year_id = session.execute(
        select(m.AcademicYear.id).join(m.School, m.School.id == m.AcademicYear.school_id).where(
            m.School.natural_id == problem.school.id, m.AcademicYear.natural_id == problem.academic_year.id,
        )
    ).scalar_one()
    group_row = session.execute(
        select(m.ParticipantGroup).where(
            m.ParticipantGroup.academic_year_id == year_id, m.ParticipantGroup.natural_id == "group_orphan",
        )
    ).scalar_one()
    membership_row = session.execute(
        select(m.ParticipantGroupClassSection).where(
            m.ParticipantGroupClassSection.academic_year_id == year_id,
            m.ParticipantGroupClassSection.participant_group_id == group_row.id,
        )
    ).scalar_one()
    session.delete(membership_row)
    session.flush()
    session.delete(group_row)
    session.commit()
    session.close()
    connection.close()

    def validate_u(current_problem):
        validate_update(current_problem, problem.school.id, problem.academic_year.id, "class_orphan", name="Renamed")

    with pytest.raises(CanonicalWholeClassGroupInvariantError):
        repo.update(problem.school.id, problem.academic_year.id, "class_orphan", "Renamed", validate=validate_u)

    reloaded = SessionFactorySchedulingProblemRepository(session_factory).load_by_school_and_year(
        problem.school.id, problem.academic_year.id,
    )
    unchanged_class = next(c for c in reloaded.class_sections if c.id == "class_orphan")
    assert unchanged_class.name == "Orphan"  # never renamed


# -- B. generation-vs-write race (Owner Decision #36) --------------------

def test_generation_persist_aborts_when_class_write_committed_since_load(seeded_db, live_db_engine):
    """Sequential, fully deterministic simulation of: (1) generation
    loads problem P; (2) a Class write commits; (3) generation attempts
    to persist against the now-stale P. Mirrors
    `test_teacher_repository.py`'s identical proof, now for a Class
    mutation -- `SchedulingProblem.class_sections` and
    `.participant_groups` are themselves part of the frozen-dataclass
    equality the generation persist step compares."""
    problem, session_factory = seeded_db
    schedule_repo = SqlAlchemyScheduleVersionRepository(session_factory)
    class_repo = SqlAlchemyClassSectionRepository(session_factory)

    stale_problem = SessionFactorySchedulingProblemRepository(session_factory).load_by_school_and_year(
        problem.school.id, problem.academic_year.id,
    )

    def validate(current_problem):
        validate_create(current_problem, problem.school.id, problem.academic_year.id, name="10-A")

    class_repo.create(
        problem.school.id, problem.academic_year.id, "class_committed_during_generation",
        "group_committed_during_generation", "10-A", validate=validate,
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


def test_generation_persist_succeeds_then_blocks_a_waiting_class_write(seeded_db):
    """Opposite ordering: generation reaches the final locked persist
    first and commits; a Class write attempted afterward must see the
    now-generated schedule and be rejected under Decision #35, with the
    configuration left unchanged."""
    problem, session_factory = seeded_db
    schedule_repo = SqlAlchemyScheduleVersionRepository(session_factory)
    class_repo = SqlAlchemyClassSectionRepository(session_factory)

    loaded_problem = SessionFactorySchedulingProblemRepository(session_factory).load_by_school_and_year(
        problem.school.id, problem.academic_year.id,
    )

    schedule_repo.persist_initial_version(
        problem.school.id, problem.academic_year.id, loaded_problem,
        entries=(), solver_status=SolverStatus.OPTIMAL, total_soft_penalty=0,
        wall_time_seconds=0.01, random_seed=None,
    )

    def validate(current_problem):
        validate_create(current_problem, problem.school.id, problem.academic_year.id, name="10-A")

    with pytest.raises(ConfigurationLockedError):
        class_repo.create(
            problem.school.id, problem.academic_year.id, "class_after_generation",
            "group_after_generation", "10-A", validate=validate,
        )

    after_ids = _class_ids(session_factory, problem.school.id, problem.academic_year.id)
    assert "class_after_generation" not in after_ids
