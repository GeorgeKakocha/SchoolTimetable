"""Real-PostgreSQL integration + concurrency tests for Resources Slice
A's Resource catalog write path:
`persistence.resource_repository.SqlAlchemyResourceRepository` and the
Owner-Decision-#36 generation-vs-write race closure, now proven for a
Resource write too (already proven for Teacher/Class/Teaching
Assignment/Subject/Special Activity writes in the sibling
`test_*_repository.py` files).

Everything here is seeded with a real, committed transaction (like
`test_teacher_repository.py`, for the same reason: some tests need two
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
    ResourceInUseError,
    ResourceNotFoundError,
)
from school_timetable.application.resource_rules import validate_create, validate_delete, validate_update
from school_timetable.domain.result import SolverStatus
from school_timetable.fixtures.valid_fixture import build_valid_fixture
from school_timetable.persistence import models as m
from school_timetable.persistence.problem_repository import SessionFactorySchedulingProblemRepository
from school_timetable.persistence.resource_repository import SqlAlchemyResourceRepository
from school_timetable.persistence.schedule_repository import SqlAlchemyScheduleVersionRepository
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


def _resource_ids(session_factory, school_id, year_id):
    problem = SessionFactorySchedulingProblemRepository(session_factory).load_by_school_and_year(school_id, year_id)
    return {r.id for r in problem.resources}


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
    repo = SqlAlchemyResourceRepository(session_factory)

    def validate(current_problem):
        validate_create(
            current_problem, problem.school.id, problem.academic_year.id, name="Music Room", capacity=2,
        )

    repo.create(
        problem.school.id, problem.academic_year.id, "resource_test_create", "Music Room", 2, validate=validate,
    )

    reloaded = SessionFactorySchedulingProblemRepository(session_factory).load_by_school_and_year(
        problem.school.id, problem.academic_year.id,
    )
    created = next(r for r in reloaded.resources if r.id == "resource_test_create")
    assert created.name == "Music Room"
    assert created.capacity == 2


def test_create_next_ordinal_is_max_plus_one(seeded_db, live_db_engine):
    problem, session_factory = seeded_db
    repo = SqlAlchemyResourceRepository(session_factory)

    def validate(current_problem):
        validate_create(
            current_problem, problem.school.id, problem.academic_year.id, name="Music Room", capacity=1,
        )

    repo.create(
        problem.school.id, problem.academic_year.id, "resource_test_ordinal", "Music Room", 1, validate=validate,
    )

    year_id = _year_id(session_factory, problem.school.id, problem.academic_year.id)
    connection = live_db_engine.connect()
    session = Session(bind=connection)
    ordinals = session.execute(
        select(m.Resource.ordinal).where(m.Resource.academic_year_id == year_id).order_by(m.Resource.ordinal)
    ).scalars().all()
    session.close()
    connection.close()
    assert ordinals == list(range(len(problem.resources) + 1))


def test_create_validation_failure_leaves_no_partial_mutation(seeded_db):
    """Atomicity proof A: create validation failure -- no Resource
    inserted."""
    problem, session_factory = seeded_db
    repo = SqlAlchemyResourceRepository(session_factory)
    before_ids = _resource_ids(session_factory, problem.school.id, problem.academic_year.id)

    def failing_validate(current_problem):
        raise ResourceNotFoundError(problem.school.id, problem.academic_year.id, "irrelevant")

    with pytest.raises(ResourceNotFoundError):
        repo.create(
            problem.school.id, problem.academic_year.id, "resource_should_not_exist",
            "Music Room", 1, validate=failing_validate,
        )

    after_ids = _resource_ids(session_factory, problem.school.id, problem.academic_year.id)
    assert after_ids == before_ids
    assert "resource_should_not_exist" not in after_ids


def test_update_round_trip(seeded_db):
    problem, session_factory = seeded_db
    repo = SqlAlchemyResourceRepository(session_factory)

    def validate(current_problem):
        validate_update(
            current_problem, problem.school.id, problem.academic_year.id, "gym", name="Sports Hall", capacity=3,
        )

    repo.update(problem.school.id, problem.academic_year.id, "gym", "Sports Hall", 3, validate=validate)

    reloaded = SessionFactorySchedulingProblemRepository(session_factory).load_by_school_and_year(
        problem.school.id, problem.academic_year.id,
    )
    updated = next(r for r in reloaded.resources if r.id == "gym")
    assert updated.name == "Sports Hall"
    assert updated.capacity == 3


def test_update_preserves_ordinal_and_id(seeded_db, live_db_engine):
    problem, session_factory = seeded_db
    repo = SqlAlchemyResourceRepository(session_factory)
    year_id = _year_id(session_factory, problem.school.id, problem.academic_year.id)

    connection = live_db_engine.connect()
    session = Session(bind=connection)
    before = session.execute(
        select(m.Resource).where(m.Resource.academic_year_id == year_id, m.Resource.natural_id == "gym")
    ).scalar_one()
    id_before, ordinal_before = before.id, before.ordinal
    session.close()
    connection.close()

    def validate(current_problem):
        validate_update(
            current_problem, problem.school.id, problem.academic_year.id, "gym", name="Renamed", capacity=1,
        )

    repo.update(problem.school.id, problem.academic_year.id, "gym", "Renamed", 1, validate=validate)

    connection2 = live_db_engine.connect()
    session2 = Session(bind=connection2)
    after = session2.execute(
        select(m.Resource).where(m.Resource.academic_year_id == year_id, m.Resource.natural_id == "gym")
    ).scalar_one()
    session2.close()
    connection2.close()

    assert after.id == id_before
    assert after.ordinal == ordinal_before


def test_update_validation_failure_leaves_name_and_capacity_unchanged(seeded_db):
    """Atomicity proof B: update validation failure -- name/capacity
    unchanged."""
    problem, session_factory = seeded_db
    repo = SqlAlchemyResourceRepository(session_factory)

    def failing_validate(current_problem):
        raise ResourceNotFoundError(problem.school.id, problem.academic_year.id, "gym")

    with pytest.raises(ResourceNotFoundError):
        repo.update(problem.school.id, problem.academic_year.id, "gym", "Renamed", 5, validate=failing_validate)

    reloaded = SessionFactorySchedulingProblemRepository(session_factory).load_by_school_and_year(
        problem.school.id, problem.academic_year.id,
    )
    unchanged = next(r for r in reloaded.resources if r.id == "gym")
    assert unchanged.name == "Indoor Gym"
    assert unchanged.capacity == 1


def test_delete_round_trip(seeded_db):
    problem, session_factory = seeded_db
    repo = SqlAlchemyResourceRepository(session_factory)

    def validate_c(current_problem):
        validate_create(current_problem, problem.school.id, problem.academic_year.id, name="Unused", capacity=1)

    repo.create(problem.school.id, problem.academic_year.id, "resource_to_delete", "Unused", 1, validate=validate_c)

    def validate_d(current_problem):
        validate_delete(current_problem, problem.school.id, problem.academic_year.id, "resource_to_delete")

    repo.delete(problem.school.id, problem.academic_year.id, "resource_to_delete", validate=validate_d)

    remaining_ids = _resource_ids(session_factory, problem.school.id, problem.academic_year.id)
    assert "resource_to_delete" not in remaining_ids


def test_delete_blocker_leaves_nothing_deleted(seeded_db):
    """Atomicity proof C: delete blocked -- Resource remains, reference
    remains."""
    problem, session_factory = seeded_db
    repo = SqlAlchemyResourceRepository(session_factory)

    def validate(current_problem):
        validate_delete(current_problem, problem.school.id, problem.academic_year.id, "gym")

    with pytest.raises(ResourceInUseError):
        repo.delete(problem.school.id, problem.academic_year.id, "gym", validate=validate)

    remaining_ids = _resource_ids(session_factory, problem.school.id, problem.academic_year.id)
    assert "gym" in remaining_ids


def test_duplicate_name_query_is_scoped_to_resource_catalog_only(seeded_db):
    """"Mathematics" is an ordinary Subject in the fixture -- creating a
    Resource of the exact same name must succeed, proving the duplicate
    check is scoped only to the Resource catalog."""
    problem, session_factory = seeded_db
    repo = SqlAlchemyResourceRepository(session_factory)

    def validate(current_problem):
        validate_create(current_problem, problem.school.id, problem.academic_year.id, name="Mathematics", capacity=1)

    repo.create(problem.school.id, problem.academic_year.id, "resource_math_room", "Mathematics", 1, validate=validate)

    reloaded = SessionFactorySchedulingProblemRepository(session_factory).load_by_school_and_year(
        problem.school.id, problem.academic_year.id,
    )
    created = next(r for r in reloaded.resources if r.id == "resource_math_room")
    assert created.name == "Mathematics"


def test_teaching_requirement_reference_detection(seeded_db):
    """`gym` is referenced by every Sport/Dance `TeachingRequirement` in
    the fixture -- `find_resource_references` (invoked via
    `validate_delete`) must report `TEACHING_REQUIREMENT`."""
    problem, session_factory = seeded_db
    repo = SqlAlchemyResourceRepository(session_factory)

    def validate(current_problem):
        validate_delete(current_problem, problem.school.id, problem.academic_year.id, "gym")

    with pytest.raises(ResourceInUseError) as exc_info:
        repo.delete(problem.school.id, problem.academic_year.id, "gym", validate=validate)
    assert exc_info.value.referenced_by == ("TEACHING_REQUIREMENT",)


def test_reserved_block_reference_detection(seeded_db, live_db_engine):
    """Resources B2: a Resource referenced only by a `ReservedBlock`
    (never any `TeachingRequirement`) must also block delete, reported
    as `RESERVED_BLOCK` -- proven end-to-end against the real
    `SqlAlchemyReservedActivityRepository`-written schema shape (a
    directly-inserted `Resource` + `ReservedBlock` row here, since this
    file's own `seeded_db` only writes the base `valid_fixture`)."""
    problem, session_factory = seeded_db
    year_id = _year_id(session_factory, problem.school.id, problem.academic_year.id)

    connection = live_db_engine.connect()
    session = Session(bind=connection)
    revision_id = session.get(m.AcademicYear, year_id).draft_revision_id
    session.add(m.Resource(
        academic_year_id=year_id, configuration_revision_id=revision_id,
        natural_id="hall", name="Assembly Hall", capacity=1, ordinal=999,
    ))
    session.flush()
    activity_row = session.execute(
        select(m.Activity).where(m.Activity.academic_year_id == year_id, m.Activity.natural_id == "club_chess")
    ).scalar_one()
    class_row = session.execute(
        select(m.ClassSection).where(m.ClassSection.academic_year_id == year_id, m.ClassSection.natural_id == "8a")
    ).scalar_one()
    day_row = session.execute(
        select(m.Day).where(m.Day.academic_year_id == year_id, m.Day.natural_id == "fri")
    ).scalar_one()
    period_row = session.execute(
        select(m.Period).where(m.Period.academic_year_id == year_id, m.Period.natural_id == "p1")
    ).scalar_one()
    resource_row = session.execute(
        select(m.Resource).where(m.Resource.academic_year_id == year_id, m.Resource.natural_id == "hall")
    ).scalar_one()
    block_row = m.ReservedBlock(
        academic_year_id=year_id, configuration_revision_id=revision_id,
        natural_id="rb_assembly", name="Assembly", activity_id=activity_row.id,
        teacher_id=None, resource_id=resource_row.id, ordinal=999,
    )
    session.add(block_row)
    session.flush()
    session.add(m.ReservedBlockClassSection(
        academic_year_id=year_id, configuration_revision_id=revision_id,
        reserved_block_id=block_row.id, class_section_id=class_row.id, ordinal=0,
    ))
    session.add(m.ReservedBlockSlot(
        academic_year_id=year_id, configuration_revision_id=revision_id,
        reserved_block_id=block_row.id, day_id=day_row.id, period_id=period_row.id,
        ordinal=0,
    ))
    session.commit()
    session.close()
    connection.close()

    repo = SqlAlchemyResourceRepository(session_factory)

    def validate(current_problem):
        validate_delete(current_problem, problem.school.id, problem.academic_year.id, "hall")

    with pytest.raises(ResourceInUseError) as exc_info:
        repo.delete(problem.school.id, problem.academic_year.id, "hall", validate=validate)
    assert exc_info.value.referenced_by == ("RESERVED_BLOCK",)


# -- B. cross-AY defense-in-depth ------------------------------------------

def test_same_natural_id_isolation_across_academic_years(seeded_db, live_db_engine):
    """A second School/AcademicYear with an overlapping resource natural
    ID must never be affected by a write scoped to the first year."""
    problem, session_factory = seeded_db

    connection = live_db_engine.connect()
    session = Session(bind=connection)
    school2 = m.School(natural_id="other-school-resource-crud", name="Other School")
    session.add(school2)
    session.flush()
    year2 = m.AcademicYear(school_id=school2.id, natural_id="other-year", label="Other Year")
    session.add(year2)
    session.flush()
    revision2_id = create_draft_configuration_revision(session, year2.id)
    session.add(m.Resource(
        academic_year_id=year2.id, configuration_revision_id=revision2_id, natural_id="resource_test_create",
        name="Other Room", capacity=1, ordinal=0,
    ))
    session.commit()
    school2_id = school2.id
    year2_id = year2.id
    session.close()
    connection.close()

    try:
        repo = SqlAlchemyResourceRepository(session_factory)

        def validate(current_problem):
            validate_create(
                current_problem, problem.school.id, problem.academic_year.id, name="Music Room", capacity=1,
            )

        repo.create(
            problem.school.id, problem.academic_year.id, "resource_test_create", "Music Room", 1, validate=validate,
        )

        first_year_ids = _resource_ids(session_factory, problem.school.id, problem.academic_year.id)
        assert "resource_test_create" in first_year_ids

        check_connection = live_db_engine.connect()
        check_session = Session(bind=check_connection)
        second_year_resource = check_session.execute(
            select(m.Resource).where(
                m.Resource.academic_year_id == year2_id, m.Resource.natural_id == "resource_test_create",
            )
        ).scalar_one()
        check_session.close()
        check_connection.close()
        # The second year's own pre-existing row is untouched.
        assert second_year_resource.name == "Other Room"
    finally:
        cleanup_connection = live_db_engine.connect()
        cleanup_session = Session(bind=cleanup_connection)
        row = cleanup_session.get(m.School, school2_id)
        if row is not None:
            cleanup_session.delete(row)
            cleanup_session.commit()
        cleanup_session.close()
        cleanup_connection.close()


def test_update_cannot_target_a_resource_in_a_different_academic_year(seeded_db, live_db_engine):
    """`ResourceService`'s own validate closure (fixture-derived
    `SchedulingProblem`) would already reject an unknown ID -- this test
    proves the repository's own `resolve_year_id`/row-lookup scoping is
    independently sufficient, per-layer defense-in-depth."""
    problem, session_factory = seeded_db

    connection = live_db_engine.connect()
    session = Session(bind=connection)
    school2 = m.School(natural_id="other-school-resource-update", name="Other School")
    session.add(school2)
    session.flush()
    year2 = m.AcademicYear(school_id=school2.id, natural_id="other-year", label="Other Year")
    session.add(year2)
    session.flush()
    revision2_id = create_draft_configuration_revision(session, year2.id)
    session.add(m.Resource(
        academic_year_id=year2.id, configuration_revision_id=revision2_id, natural_id="resource_cross_ay_target",
        name="Cross AY Room", capacity=1, ordinal=0,
    ))
    session.commit()
    school2_id = school2.id
    year2_id = year2.id
    session.close()
    connection.close()

    try:
        repo = SqlAlchemyResourceRepository(session_factory)

        def permissive_validate(current_problem):
            pass  # Deliberately does not check existence, to prove the repository's own row-lookup guard.

        with pytest.raises(ResourceNotFoundError):
            repo.update(
                problem.school.id, problem.academic_year.id, "resource_cross_ay_target",
                "Hijacked", 1, validate=permissive_validate,
            )

        check_connection = live_db_engine.connect()
        check_session = Session(bind=check_connection)
        untouched = check_session.execute(
            select(m.Resource).where(
                m.Resource.academic_year_id == year2_id, m.Resource.natural_id == "resource_cross_ay_target",
            )
        ).scalar_one()
        check_session.close()
        check_connection.close()
        assert untouched.name == "Cross AY Room"
    finally:
        cleanup_connection = live_db_engine.connect()
        cleanup_session = Session(bind=cleanup_connection)
        row = cleanup_session.get(m.School, school2_id)
        if row is not None:
            cleanup_session.delete(row)
            cleanup_session.commit()
        cleanup_session.close()
        cleanup_connection.close()


def test_delete_cannot_target_a_resource_in_a_different_academic_year(seeded_db, live_db_engine):
    problem, session_factory = seeded_db

    connection = live_db_engine.connect()
    session = Session(bind=connection)
    school2 = m.School(natural_id="other-school-resource-delete", name="Other School")
    session.add(school2)
    session.flush()
    year2 = m.AcademicYear(school_id=school2.id, natural_id="other-year", label="Other Year")
    session.add(year2)
    session.flush()
    revision2_id = create_draft_configuration_revision(session, year2.id)
    session.add(m.Resource(
        academic_year_id=year2.id, configuration_revision_id=revision2_id, natural_id="resource_cross_ay_delete_target",
        name="Cross AY Room", capacity=1, ordinal=0,
    ))
    session.commit()
    school2_id = school2.id
    year2_id = year2.id
    session.close()
    connection.close()

    try:
        repo = SqlAlchemyResourceRepository(session_factory)

        def permissive_validate(current_problem):
            pass

        with pytest.raises(ResourceNotFoundError):
            repo.delete(
                problem.school.id, problem.academic_year.id, "resource_cross_ay_delete_target",
                validate=permissive_validate,
            )

        check_connection = live_db_engine.connect()
        check_session = Session(bind=check_connection)
        untouched = check_session.execute(
            select(m.Resource).where(
                m.Resource.academic_year_id == year2_id, m.Resource.natural_id == "resource_cross_ay_delete_target",
            )
        ).scalar_one()
        check_session.close()
        check_connection.close()
        assert untouched.name == "Cross AY Room"
    finally:
        cleanup_connection = live_db_engine.connect()
        cleanup_session = Session(bind=cleanup_connection)
        row = cleanup_session.get(m.School, school2_id)
        if row is not None:
            cleanup_session.delete(row)
            cleanup_session.commit()
        cleanup_session.close()
        cleanup_connection.close()


# -- C. generation-vs-write race (Owner Decision #36) --------------------

def test_generation_persist_aborts_when_resource_write_committed_since_load(seeded_db, live_db_engine):
    """Sequential, fully deterministic simulation of: (1) generation
    loads problem P; (2) a Resource write commits; (3) generation
    attempts to persist against the now-stale P. Mirrors
    `test_special_activity_repository.py`'s identical proof, now for a
    Resource mutation -- `SchedulingProblem.resources` is part of the
    frozen-dataclass equality the generation persist step compares."""
    problem, session_factory = seeded_db
    schedule_repo = SqlAlchemyScheduleVersionRepository(session_factory)
    resource_repo = SqlAlchemyResourceRepository(session_factory)

    stale_problem = SessionFactorySchedulingProblemRepository(session_factory).load_by_school_and_year(
        problem.school.id, problem.academic_year.id,
    )

    def validate(current_problem):
        validate_update(
            current_problem, problem.school.id, problem.academic_year.id, "gym", name="Sports Hall", capacity=2,
        )

    resource_repo.update(problem.school.id, problem.academic_year.id, "gym", "Sports Hall", 2, validate=validate)

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


def test_generation_persist_succeeds_then_blocks_a_waiting_resource_write(seeded_db):
    """Opposite ordering: generation reaches the final locked persist
    first and commits; a Resource write attempted afterward must see
    the now-generated schedule and be rejected under Decision #35, with
    the configuration left unchanged."""
    problem, session_factory = seeded_db
    schedule_repo = SqlAlchemyScheduleVersionRepository(session_factory)
    resource_repo = SqlAlchemyResourceRepository(session_factory)

    loaded_problem = SessionFactorySchedulingProblemRepository(session_factory).load_by_school_and_year(
        problem.school.id, problem.academic_year.id,
    )

    schedule_repo.persist_initial_version(
        problem.school.id, problem.academic_year.id, loaded_problem,
        entries=(), solver_status=SolverStatus.OPTIMAL, total_soft_penalty=0,
        wall_time_seconds=0.01, random_seed=None,
    )

    def validate(current_problem):
        validate_update(
            current_problem, problem.school.id, problem.academic_year.id, "gym", name="Sports Hall", capacity=2,
        )

    with pytest.raises(ConfigurationLockedError):
        resource_repo.update(problem.school.id, problem.academic_year.id, "gym", "Sports Hall", 2, validate=validate)

    reloaded = SessionFactorySchedulingProblemRepository(session_factory).load_by_school_and_year(
        problem.school.id, problem.academic_year.id,
    )
    unchanged = next(r for r in reloaded.resources if r.id == "gym")
    assert unchanged.name == "Indoor Gym"
    assert unchanged.capacity == 1
