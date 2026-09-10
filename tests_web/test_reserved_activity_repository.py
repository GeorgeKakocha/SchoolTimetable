"""Real-PostgreSQL integration + concurrency tests for Reserved
Activities Slice A2's Reserved Activity write path:
`persistence.reserved_activity_repository.SqlAlchemyReservedActivityRepository`
and the Owner-Decision-#36 generation-vs-write race closure, now proven
for a Reserved Activity write too.

Everything here is seeded with a real, committed transaction (like
`test_special_activity_repository.py`, for the same reason: some tests
need two independent, overlapping database transactions), and the
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
    NonSpecialActivityTargetError,
    ReservedActivityNotFoundError,
    UnknownReferenceError,
)
from school_timetable.application.reserved_activity_models import ReservedActivityFields, ReservedActivitySlotFields
from school_timetable.application.reserved_activity_rules import validate_create, validate_delete, validate_update
from school_timetable.domain.activities import Activity, ActivityKind
from school_timetable.domain.blocks import ReservedBlock
from school_timetable.domain.calendar import AcademicYear, Day, Period, TimeSlot
from school_timetable.domain.groups import ClassSection, ParticipantGroup, ParticipantGroupRole
from school_timetable.domain.people import Teacher
from school_timetable.domain.problem import SchedulingProblem
from school_timetable.domain.result import SolverStatus
from school_timetable.domain.school import School
from school_timetable.fixtures.valid_fixture import build_valid_fixture
from school_timetable.persistence import models as m
from school_timetable.persistence.problem_repository import SessionFactorySchedulingProblemRepository
from school_timetable.persistence.reserved_activity_repository import SqlAlchemyReservedActivityRepository
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


def _other_year_problem() -> SchedulingProblem:
    """A second, wholly independent school/AcademicYear -- used only by
    the cross-AY defense-in-depth tests below to prove the repository
    itself (not merely the application-layer `validate` callback)
    refuses to resolve a natural ID belonging to a DIFFERENT
    `academic_year_id`."""
    return SchedulingProblem(
        school=School(id="reserved-activity-other-ay-school", name="Other AY School"),
        academic_year=AcademicYear(id="ay-reserved-activity-other", label="Other"),
        days=(Day(id="d1", name="D1", index=0),),
        periods=(Period(id="p1", name="P1", index=0, block_id="blk", is_instructional=True),),
        teachers=(Teacher(id="t_other", first_name="T Other", last_name=""),),
        class_sections=(ClassSection(id="c_other", name="C Other"),),
        participant_groups=(
            ParticipantGroup(id="pg_other", name="PG Other", class_sections=("c_other",), role=ParticipantGroupRole.WHOLE_CLASS),
        ),
        activities=(
            Activity(id="club_other", name="Club Other", kind=ActivityKind.CLUB),
            Activity(id="ordinary_other", name="Ordinary Other", kind=ActivityKind.ORDINARY),
        ),
        teaching_requirements=(),
        reserved_blocks=(
            ReservedBlock(
                id="rb_other_year", name="Club Other", activity_id="club_other",
                class_sections=("c_other",), slots=(TimeSlot("d1", "p1"),),
            ),
        ),
    )


@pytest.fixture
def seeded_db_two_years(live_db_engine):
    """Like `seeded_db`, but also seeds `_other_year_problem()` as a
    second, wholly independent school/AcademicYear in the SAME
    database -- exclusively for the cross-AY defense-in-depth tests."""
    connection = live_db_engine.connect()
    session = Session(bind=connection)
    problem = build_valid_fixture()
    other_problem = _other_year_problem()
    write_scheduling_problem(session, problem)
    write_scheduling_problem(session, other_problem)
    session.commit()
    session.close()
    connection.close()

    session_factory = sessionmaker(bind=live_db_engine, autoflush=False, autocommit=False, expire_on_commit=False)

    try:
        yield problem, other_problem, session_factory
    finally:
        cleanup_connection = live_db_engine.connect()
        cleanup_session = Session(bind=cleanup_connection)
        for target_problem in (problem, other_problem):
            school_row = cleanup_session.execute(
                select(m.School).where(m.School.natural_id == target_problem.school.id)
            ).scalar_one_or_none()
            if school_row is not None:
                cleanup_session.delete(school_row)
        cleanup_session.commit()
        cleanup_session.close()
        cleanup_connection.close()


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


def _fields(special_activity_id="club_robotics", class_section_ids=("8a",), teacher_id=None, slots=(("mon", "p1"),)):
    return ReservedActivityFields(
        special_activity_id=special_activity_id,
        class_section_ids=class_section_ids,
        teacher_id=teacher_id,
        slots=tuple(ReservedActivitySlotFields(day_id=d, period_id=p) for d, p in slots),
    )


def _reserved_block_ids(session_factory, school_id, year_id):
    problem = SessionFactorySchedulingProblemRepository(session_factory).load_by_school_and_year(school_id, year_id)
    return {b.id for b in problem.reserved_blocks}


def _validate_for(problem, school_id, year_id, reserved_activity_id, fields, *, is_update):
    fn = validate_update if is_update else validate_create
    def validate(current_problem):
        fn(
            current_problem, school_id, year_id, reserved_activity_id,
            special_activity_id=fields.special_activity_id, class_section_ids=fields.class_section_ids,
            teacher_id=fields.teacher_id, slots=tuple((s.day_id, s.period_id) for s in fields.slots),
        )
    return validate


# -- A. write persistence -----------------------------------------------

def test_create_round_trip(seeded_db):
    problem, session_factory = seeded_db
    repo = SqlAlchemyReservedActivityRepository(session_factory)
    fields = _fields()
    validate = _validate_for(problem, problem.school.id, problem.academic_year.id, "rb_new", fields, is_update=False)

    result = repo.create(problem.school.id, problem.academic_year.id, "rb_new", fields, validate=validate)
    assert result.id == "rb_new"
    assert result.special_activity_id == "club_robotics"
    assert result.class_section_ids == ("8a",)
    assert result.teacher_id is None

    reloaded = SessionFactorySchedulingProblemRepository(session_factory).load_by_school_and_year(
        problem.school.id, problem.academic_year.id,
    )
    created = next(b for b in reloaded.reserved_blocks if b.id == "rb_new")
    assert created.name == "Robotics Club"
    assert created.activity_id == "club_robotics"
    assert created.class_sections == ("8a",)


def test_create_derived_name_matches_special_activity(seeded_db):
    problem, session_factory = seeded_db
    repo = SqlAlchemyReservedActivityRepository(session_factory)
    fields = _fields(special_activity_id="club_chess", class_section_ids=("9a",), slots=(("tue", "p1"),))
    validate = _validate_for(problem, problem.school.id, problem.academic_year.id, "rb_new2", fields, is_update=False)

    repo.create(problem.school.id, problem.academic_year.id, "rb_new2", fields, validate=validate)

    reloaded = SessionFactorySchedulingProblemRepository(session_factory).load_by_school_and_year(
        problem.school.id, problem.academic_year.id,
    )
    created = next(b for b in reloaded.reserved_blocks if b.id == "rb_new2")
    assert created.name == "Chess Club"


def test_create_next_ordinal_is_max_plus_one(seeded_db, live_db_engine):
    problem, session_factory = seeded_db
    repo = SqlAlchemyReservedActivityRepository(session_factory)
    fields = _fields()
    validate = _validate_for(problem, problem.school.id, problem.academic_year.id, "rb_new", fields, is_update=False)
    repo.create(problem.school.id, problem.academic_year.id, "rb_new", fields, validate=validate)

    year_id = _year_id(session_factory, problem.school.id, problem.academic_year.id)
    connection = live_db_engine.connect()
    session = Session(bind=connection)
    ordinals = session.execute(
        select(m.ReservedBlock.ordinal).where(m.ReservedBlock.academic_year_id == year_id)
        .order_by(m.ReservedBlock.ordinal)
    ).scalars().all()
    session.close()
    connection.close()
    # Fixture has 2 pre-existing blocks (club_chess, club_robotics) ->
    # ordinals 0, 1; new one is 2.
    assert ordinals == [0, 1, 2]


def test_create_class_canonical_order_regardless_of_request_order(seeded_db):
    problem, session_factory = seeded_db
    repo = SqlAlchemyReservedActivityRepository(session_factory)
    # Request classes in reverse-of-authoritative order (fixture order
    # is 8a, 8b, 9a, 9b).
    fields = _fields(special_activity_id="club_robotics", class_section_ids=("9b", "8a"), slots=(("fri", "p1"),))
    validate = _validate_for(problem, problem.school.id, problem.academic_year.id, "rb_order", fields, is_update=False)

    result = repo.create(problem.school.id, problem.academic_year.id, "rb_order", fields, validate=validate)
    assert result.class_section_ids == ("8a", "9b")


def test_create_slot_canonical_order_regardless_of_request_order(seeded_db):
    problem, session_factory = seeded_db
    repo = SqlAlchemyReservedActivityRepository(session_factory)
    # Request slots out of Day.index/Period.index order.
    fields = _fields(
        special_activity_id="club_robotics", class_section_ids=("8a",),
        slots=(("fri", "p2"), ("mon", "p1")),
    )
    validate = _validate_for(problem, problem.school.id, problem.academic_year.id, "rb_order2", fields, is_update=False)

    result = repo.create(problem.school.id, problem.academic_year.id, "rb_order2", fields, validate=validate)
    assert [(s.day_id, s.period_id) for s in result.slots] == [("mon", "p1"), ("fri", "p2")]


def test_create_teacher_set(seeded_db):
    problem, session_factory = seeded_db
    repo = SqlAlchemyReservedActivityRepository(session_factory)
    fields = _fields(teacher_id="t_art", slots=(("fri", "p3"),))
    validate = _validate_for(problem, problem.school.id, problem.academic_year.id, "rb_teacher", fields, is_update=False)

    result = repo.create(problem.school.id, problem.academic_year.id, "rb_teacher", fields, validate=validate)
    assert result.teacher_id == "t_art"


def test_create_only_requested_ay_touched(seeded_db, live_db_engine):
    problem, session_factory = seeded_db
    repo = SqlAlchemyReservedActivityRepository(session_factory)
    fields = _fields()
    validate = _validate_for(problem, problem.school.id, problem.academic_year.id, "rb_new", fields, is_update=False)
    repo.create(problem.school.id, problem.academic_year.id, "rb_new", fields, validate=validate)

    connection = live_db_engine.connect()
    session = Session(bind=connection)
    row = session.execute(
        select(m.ReservedBlock).where(m.ReservedBlock.natural_id == "rb_new")
    ).scalar_one()
    year_id = _year_id(session_factory, problem.school.id, problem.academic_year.id)
    session.close()
    connection.close()
    assert row.academic_year_id == year_id


def test_create_validation_failure_leaves_no_partial_mutation(seeded_db):
    problem, session_factory = seeded_db
    repo = SqlAlchemyReservedActivityRepository(session_factory)
    before_ids = _reserved_block_ids(session_factory, problem.school.id, problem.academic_year.id)

    def failing_validate(current_problem):
        raise ReservedActivityNotFoundError(problem.school.id, problem.academic_year.id, "irrelevant")

    with pytest.raises(ReservedActivityNotFoundError):
        repo.create(
            problem.school.id, problem.academic_year.id, "rb_should_not_exist", _fields(), validate=failing_validate,
        )

    after_ids = _reserved_block_ids(session_factory, problem.school.id, problem.academic_year.id)
    assert after_ids == before_ids
    assert "rb_should_not_exist" not in after_ids


def test_update_round_trip(seeded_db):
    problem, session_factory = seeded_db
    repo = SqlAlchemyReservedActivityRepository(session_factory)
    fields = _fields(special_activity_id="club_chess", class_section_ids=("9a", "9b"), slots=(("thu", "p1"),))
    validate = _validate_for(problem, problem.school.id, problem.academic_year.id, "club_chess", fields, is_update=True)

    result = repo.update(problem.school.id, problem.academic_year.id, "club_chess", fields, validate=validate)
    assert result.id == "club_chess"
    assert result.class_section_ids == ("9a", "9b")

    reloaded = SessionFactorySchedulingProblemRepository(session_factory).load_by_school_and_year(
        problem.school.id, problem.academic_year.id,
    )
    updated = next(b for b in reloaded.reserved_blocks if b.id == "club_chess")
    assert updated.class_sections == ("9a", "9b")
    assert updated.slots == (updated.slots[0],)
    assert updated.slots[0].day_id == "thu"


def test_update_preserves_natural_id_and_ordinal(seeded_db, live_db_engine):
    problem, session_factory = seeded_db
    repo = SqlAlchemyReservedActivityRepository(session_factory)
    year_id = _year_id(session_factory, problem.school.id, problem.academic_year.id)

    connection = live_db_engine.connect()
    session = Session(bind=connection)
    before = session.execute(
        select(m.ReservedBlock).where(
            m.ReservedBlock.academic_year_id == year_id, m.ReservedBlock.natural_id == "club_chess",
        )
    ).scalar_one()
    ordinal_before = before.ordinal
    session.close()
    connection.close()

    fields = _fields(special_activity_id="club_chess", class_section_ids=("9a",), slots=(("thu", "p1"),))
    validate = _validate_for(problem, problem.school.id, problem.academic_year.id, "club_chess", fields, is_update=True)
    repo.update(problem.school.id, problem.academic_year.id, "club_chess", fields, validate=validate)

    connection2 = live_db_engine.connect()
    session2 = Session(bind=connection2)
    after = session2.execute(
        select(m.ReservedBlock).where(
            m.ReservedBlock.academic_year_id == year_id, m.ReservedBlock.natural_id == "club_chess",
        )
    ).scalar_one()
    session2.close()
    connection2.close()
    assert after.ordinal == ordinal_before


def test_update_old_children_replaced(seeded_db, live_db_engine):
    problem, session_factory = seeded_db
    repo = SqlAlchemyReservedActivityRepository(session_factory)
    year_id = _year_id(session_factory, problem.school.id, problem.academic_year.id)

    connection = live_db_engine.connect()
    session = Session(bind=connection)
    block_row = session.execute(
        select(m.ReservedBlock).where(
            m.ReservedBlock.academic_year_id == year_id, m.ReservedBlock.natural_id == "club_chess",
        )
    ).scalar_one()
    block_surrogate_id = block_row.id
    session.close()
    connection.close()

    fields = _fields(special_activity_id="club_chess", class_section_ids=("9a",), slots=(("thu", "p1"),))
    validate = _validate_for(problem, problem.school.id, problem.academic_year.id, "club_chess", fields, is_update=True)
    repo.update(problem.school.id, problem.academic_year.id, "club_chess", fields, validate=validate)

    connection2 = live_db_engine.connect()
    session2 = Session(bind=connection2)
    class_rows = session2.execute(
        select(m.ReservedBlockClassSection).where(
            m.ReservedBlockClassSection.reserved_block_id == block_surrogate_id,
        )
    ).scalars().all()
    slot_rows = session2.execute(
        select(m.ReservedBlockSlot).where(m.ReservedBlockSlot.reserved_block_id == block_surrogate_id)
    ).scalars().all()
    session2.close()
    connection2.close()
    assert len(class_rows) == 1
    assert len(slot_rows) == 1


def test_update_teacher_null_to_non_null(seeded_db):
    problem, session_factory = seeded_db
    repo = SqlAlchemyReservedActivityRepository(session_factory)
    fields = _fields(special_activity_id="club_chess", class_section_ids=("8a", "8b"), teacher_id="t_art", slots=(("wed", "p8"),))
    validate = _validate_for(problem, problem.school.id, problem.academic_year.id, "club_chess", fields, is_update=True)

    result = repo.update(problem.school.id, problem.academic_year.id, "club_chess", fields, validate=validate)
    assert result.teacher_id == "t_art"


def test_update_teacher_non_null_to_null(seeded_db):
    problem, session_factory = seeded_db
    repo = SqlAlchemyReservedActivityRepository(session_factory)
    fields1 = _fields(special_activity_id="club_chess", class_section_ids=("8a", "8b"), teacher_id="t_art", slots=(("wed", "p8"),))
    validate1 = _validate_for(problem, problem.school.id, problem.academic_year.id, "club_chess", fields1, is_update=True)
    repo.update(problem.school.id, problem.academic_year.id, "club_chess", fields1, validate=validate1)

    reloaded = SessionFactorySchedulingProblemRepository(session_factory).load_by_school_and_year(
        problem.school.id, problem.academic_year.id,
    )
    fields2 = _fields(special_activity_id="club_chess", class_section_ids=("8a", "8b"), teacher_id=None, slots=(("wed", "p8"),))
    validate2 = _validate_for(reloaded, problem.school.id, problem.academic_year.id, "club_chess", fields2, is_update=True)
    result = repo.update(problem.school.id, problem.academic_year.id, "club_chess", fields2, validate=validate2)
    assert result.teacher_id is None


def test_update_activity_change_updates_fk_and_recomputes_name(seeded_db, live_db_engine):
    problem, session_factory = seeded_db
    repo = SqlAlchemyReservedActivityRepository(session_factory)
    fields = _fields(special_activity_id="club_robotics", class_section_ids=("8a", "8b"), slots=(("wed", "p8"),))
    validate = _validate_for(problem, problem.school.id, problem.academic_year.id, "club_chess", fields, is_update=True)

    repo.update(problem.school.id, problem.academic_year.id, "club_chess", fields, validate=validate)

    year_id = _year_id(session_factory, problem.school.id, problem.academic_year.id)
    connection = live_db_engine.connect()
    session = Session(bind=connection)
    row = session.execute(
        select(m.ReservedBlock).where(
            m.ReservedBlock.academic_year_id == year_id, m.ReservedBlock.natural_id == "club_chess",
        )
    ).scalar_one()
    session.close()
    connection.close()
    assert row.name == "Robotics Club"


def test_update_unrelated_blocks_untouched(seeded_db):
    problem, session_factory = seeded_db
    repo = SqlAlchemyReservedActivityRepository(session_factory)
    fields = _fields(special_activity_id="club_chess", class_section_ids=("9a",), slots=(("thu", "p1"),))
    validate = _validate_for(problem, problem.school.id, problem.academic_year.id, "club_chess", fields, is_update=True)
    repo.update(problem.school.id, problem.academic_year.id, "club_chess", fields, validate=validate)

    reloaded = SessionFactorySchedulingProblemRepository(session_factory).load_by_school_and_year(
        problem.school.id, problem.academic_year.id,
    )
    robotics = next(b for b in reloaded.reserved_blocks if b.id == "club_robotics")
    assert robotics.class_sections == ("9a", "9b")
    assert robotics.slots[0].day_id == "thu" and robotics.slots[0].period_id == "p8"


def test_update_validation_failure_leaves_aggregate_unchanged(seeded_db):
    problem, session_factory = seeded_db
    repo = SqlAlchemyReservedActivityRepository(session_factory)

    def failing_validate(current_problem):
        raise ReservedActivityNotFoundError(problem.school.id, problem.academic_year.id, "club_chess")

    with pytest.raises(ReservedActivityNotFoundError):
        repo.update(
            problem.school.id, problem.academic_year.id, "club_chess",
            _fields(special_activity_id="club_robotics", class_section_ids=("9a",), slots=(("thu", "p1"),)),
            validate=failing_validate,
        )

    reloaded = SessionFactorySchedulingProblemRepository(session_factory).load_by_school_and_year(
        problem.school.id, problem.academic_year.id,
    )
    unchanged = next(b for b in reloaded.reserved_blocks if b.id == "club_chess")
    assert unchanged.activity_id == "club_chess"
    assert unchanged.class_sections == ("8a", "8b")


def test_update_missing_target_raises_not_found(seeded_db):
    problem, session_factory = seeded_db
    repo = SqlAlchemyReservedActivityRepository(session_factory)

    def permissive_validate(current_problem):
        pass

    with pytest.raises(ReservedActivityNotFoundError):
        repo.update(
            problem.school.id, problem.academic_year.id, "no-such-block", _fields(), validate=permissive_validate,
        )


def test_delete_round_trip(seeded_db):
    problem, session_factory = seeded_db
    repo = SqlAlchemyReservedActivityRepository(session_factory)
    fields = _fields(class_section_ids=("9a",), slots=(("thu", "p2"),))
    validate_c = _validate_for(problem, problem.school.id, problem.academic_year.id, "rb_to_delete", fields, is_update=False)
    repo.create(problem.school.id, problem.academic_year.id, "rb_to_delete", fields, validate=validate_c)

    def validate_d(current_problem):
        validate_delete(current_problem, problem.school.id, problem.academic_year.id, "rb_to_delete")

    repo.delete(problem.school.id, problem.academic_year.id, "rb_to_delete", validate=validate_d)

    remaining_ids = _reserved_block_ids(session_factory, problem.school.id, problem.academic_year.id)
    assert "rb_to_delete" not in remaining_ids


def test_delete_children_cascade(seeded_db, live_db_engine):
    problem, session_factory = seeded_db
    repo = SqlAlchemyReservedActivityRepository(session_factory)

    connection = live_db_engine.connect()
    session = Session(bind=connection)
    block_row = session.execute(
        select(m.ReservedBlock).where(m.ReservedBlock.natural_id == "club_chess")
    ).scalar_one()
    block_surrogate_id = block_row.id
    session.close()
    connection.close()

    def validate_d(current_problem):
        validate_delete(current_problem, problem.school.id, problem.academic_year.id, "club_chess")

    repo.delete(problem.school.id, problem.academic_year.id, "club_chess", validate=validate_d)

    connection2 = live_db_engine.connect()
    session2 = Session(bind=connection2)
    remaining_classes = session2.execute(
        select(m.ReservedBlockClassSection).where(
            m.ReservedBlockClassSection.reserved_block_id == block_surrogate_id,
        )
    ).scalars().all()
    remaining_slots = session2.execute(
        select(m.ReservedBlockSlot).where(m.ReservedBlockSlot.reserved_block_id == block_surrogate_id)
    ).scalars().all()
    session2.close()
    connection2.close()
    assert remaining_classes == []
    assert remaining_slots == []


def test_delete_referenced_activity_teacher_classes_survive(seeded_db):
    problem, session_factory = seeded_db
    repo = SqlAlchemyReservedActivityRepository(session_factory)

    def validate_d(current_problem):
        validate_delete(current_problem, problem.school.id, problem.academic_year.id, "club_chess")

    repo.delete(problem.school.id, problem.academic_year.id, "club_chess", validate=validate_d)

    reloaded = SessionFactorySchedulingProblemRepository(session_factory).load_by_school_and_year(
        problem.school.id, problem.academic_year.id,
    )
    assert any(a.id == "club_chess" for a in reloaded.activities)
    assert any(c.id == "8a" for c in reloaded.class_sections)
    assert any(d.id for d in reloaded.days)
    assert any(p.id for p in reloaded.periods)


def test_delete_missing_target_raises_not_found(seeded_db):
    problem, session_factory = seeded_db
    repo = SqlAlchemyReservedActivityRepository(session_factory)

    def permissive_validate(current_problem):
        pass

    with pytest.raises(ReservedActivityNotFoundError):
        repo.delete(problem.school.id, problem.academic_year.id, "no-such-block", validate=permissive_validate)


# -- B. ORM defense-in-depth -----------------------------------------------

def test_create_ordinary_activity_rejected_even_with_permissive_validate(seeded_db):
    problem, session_factory = seeded_db
    repo = SqlAlchemyReservedActivityRepository(session_factory)

    def permissive_validate(current_problem):
        pass  # Deliberately does not check kind, to prove the repository's own guard.

    with pytest.raises(NonSpecialActivityTargetError):
        repo.create(
            problem.school.id, problem.academic_year.id, "rb_wrong_kind",
            _fields(special_activity_id="math"), validate=permissive_validate,
        )

    remaining_ids = _reserved_block_ids(session_factory, problem.school.id, problem.academic_year.id)
    assert "rb_wrong_kind" not in remaining_ids


def test_create_unknown_special_activity_rejected_even_with_permissive_validate(seeded_db):
    problem, session_factory = seeded_db
    repo = SqlAlchemyReservedActivityRepository(session_factory)

    def permissive_validate(current_problem):
        pass

    with pytest.raises(UnknownReferenceError):
        repo.create(
            problem.school.id, problem.academic_year.id, "rb_unknown",
            _fields(special_activity_id="no-such-activity"), validate=permissive_validate,
        )


# -- C. generation-vs-write race (Owner Decision #36) --------------------

def test_generation_persist_aborts_when_reserved_activity_write_committed_since_load(seeded_db, live_db_engine):
    """Sequential, fully deterministic simulation of: (1) generation
    loads problem P; (2) a Reserved Activity write commits; (3)
    generation attempts to persist against the now-stale P. Mirrors
    `test_special_activity_repository.py`'s identical proof, now for a
    Reserved Activity mutation -- `SchedulingProblem.reserved_blocks`
    is itself part of the frozen-dataclass equality the generation
    persist step compares."""
    problem, session_factory = seeded_db
    schedule_repo = SqlAlchemyScheduleVersionRepository(session_factory)
    reserved_activity_repo = SqlAlchemyReservedActivityRepository(session_factory)

    stale_problem = SessionFactorySchedulingProblemRepository(session_factory).load_by_school_and_year(
        problem.school.id, problem.academic_year.id,
    )

    fields = _fields(class_section_ids=("9a",), slots=(("thu", "p2"),))
    validate = _validate_for(
        problem, problem.school.id, problem.academic_year.id, "rb_committed_during_generation", fields,
        is_update=False,
    )
    reserved_activity_repo.create(
        problem.school.id, problem.academic_year.id, "rb_committed_during_generation", fields, validate=validate,
    )

    with pytest.raises(ConfigurationChangedDuringGenerationError):
        schedule_repo.persist_initial_version(
            problem.school.id, problem.academic_year.id, stale_problem,
            entries=(), solver_status=SolverStatus.OPTIMAL, total_soft_penalty=0,
            wall_time_seconds=0.01, random_seed=None,
        )

    year_id = _year_id(session_factory, problem.school.id, problem.academic_year.id)
    connection = live_db_engine.connect()
    session = Session(bind=connection)
    schedule_count = len(
        session.execute(select(m.Schedule).where(m.Schedule.academic_year_id == year_id)).scalars().all()
    )
    session.close()
    connection.close()
    assert schedule_count == 0


def test_generation_persist_succeeds_then_blocks_a_waiting_reserved_activity_write(seeded_db):
    """Opposite ordering: generation reaches the final locked persist
    first and commits; a Reserved Activity write attempted afterward
    must see the now-generated schedule and be rejected under Decision
    #35, with the configuration left unchanged."""
    problem, session_factory = seeded_db
    schedule_repo = SqlAlchemyScheduleVersionRepository(session_factory)
    reserved_activity_repo = SqlAlchemyReservedActivityRepository(session_factory)

    loaded_problem = SessionFactorySchedulingProblemRepository(session_factory).load_by_school_and_year(
        problem.school.id, problem.academic_year.id,
    )

    schedule_repo.persist_initial_version(
        problem.school.id, problem.academic_year.id, loaded_problem,
        entries=(), solver_status=SolverStatus.OPTIMAL, total_soft_penalty=0,
        wall_time_seconds=0.01, random_seed=None,
    )

    fields = _fields(class_section_ids=("9a",), slots=(("thu", "p2"),))
    validate = _validate_for(
        problem, problem.school.id, problem.academic_year.id, "rb_after_generation", fields, is_update=False,
    )

    with pytest.raises(ConfigurationLockedError):
        reserved_activity_repo.create(
            problem.school.id, problem.academic_year.id, "rb_after_generation", fields, validate=validate,
        )

    after_ids = _reserved_block_ids(session_factory, problem.school.id, problem.academic_year.id)
    assert "rb_after_generation" not in after_ids


# -- D. cross-AcademicYear defense-in-depth (pre-closure audit) ----------

def test_update_ordinary_activity_rejected_even_with_permissive_validate(seeded_db):
    """Same-AY defense-in-depth for UPDATE specifically (CREATE's own
    equivalent is already proven above) -- the repository itself
    independently re-verifies `kind == 'CLUB'`, never trusting a
    deliberately permissive `validate` callback."""
    problem, session_factory = seeded_db
    repo = SqlAlchemyReservedActivityRepository(session_factory)

    def permissive_validate(current_problem):
        pass

    with pytest.raises(NonSpecialActivityTargetError):
        repo.update(
            problem.school.id, problem.academic_year.id, "club_chess",
            _fields(special_activity_id="math", class_section_ids=("8a",), slots=(("mon", "p1"),)),
            validate=permissive_validate,
        )

    reloaded = SessionFactorySchedulingProblemRepository(session_factory).load_by_school_and_year(
        problem.school.id, problem.academic_year.id,
    )
    unchanged = next(b for b in reloaded.reserved_blocks if b.id == "club_chess")
    assert unchanged.activity_id == "club_chess"
    assert unchanged.class_sections == ("8a", "8b")


def test_update_target_from_other_academic_year_rejected(seeded_db_two_years):
    """The repository resolves the PUT/DELETE target scoped to
    `academic_year_id == year_id` -- a `ReservedBlock` natural ID that
    exists only in a DIFFERENT AcademicYear is indistinguishable from
    one that does not exist at all, even with a deliberately permissive
    `validate` callback. Both years are reconfirmed unchanged."""
    problem, other_problem, session_factory = seeded_db_two_years
    repo = SqlAlchemyReservedActivityRepository(session_factory)

    def permissive_validate(current_problem):
        pass

    with pytest.raises(ReservedActivityNotFoundError):
        repo.update(
            problem.school.id, problem.academic_year.id, "rb_other_year",
            _fields(class_section_ids=("8a",), slots=(("mon", "p1"),)),
            validate=permissive_validate,
        )

    # The other year's own block is completely untouched.
    reloaded_other = SessionFactorySchedulingProblemRepository(session_factory).load_by_school_and_year(
        other_problem.school.id, other_problem.academic_year.id,
    )
    unchanged_other = next(b for b in reloaded_other.reserved_blocks if b.id == "rb_other_year")
    assert unchanged_other.class_sections == ("c_other",)
    assert unchanged_other.slots == (unchanged_other.slots[0],)

    # The requested year gained nothing under that natural ID.
    requested_ids = _reserved_block_ids(session_factory, problem.school.id, problem.academic_year.id)
    assert "rb_other_year" not in requested_ids


def test_delete_target_from_other_academic_year_rejected(seeded_db_two_years):
    """Symmetric DELETE proof, with a deliberately permissive `validate`
    callback -- the repository, not the callback, is what refuses the
    wrong-year target."""
    problem, other_problem, session_factory = seeded_db_two_years
    repo = SqlAlchemyReservedActivityRepository(session_factory)

    def permissive_validate(current_problem):
        pass

    with pytest.raises(ReservedActivityNotFoundError):
        repo.delete(problem.school.id, problem.academic_year.id, "rb_other_year", validate=permissive_validate)

    other_ids = _reserved_block_ids(session_factory, other_problem.school.id, other_problem.academic_year.id)
    assert "rb_other_year" in other_ids


def test_update_special_activity_from_other_academic_year_rejected(seeded_db_two_years):
    """UPDATE's nested `special_activity_id` is resolved scoped to the
    SAME `academic_year_id` as the target block -- a Special Activity
    natural ID that exists only in a DIFFERENT AcademicYear is treated
    as genuinely unknown (`UnknownReferenceError`), never silently
    resolved cross-year, even with a permissive `validate` callback."""
    problem, other_problem, session_factory = seeded_db_two_years
    repo = SqlAlchemyReservedActivityRepository(session_factory)

    def permissive_validate(current_problem):
        pass

    with pytest.raises(UnknownReferenceError) as exc_info:
        repo.update(
            problem.school.id, problem.academic_year.id, "club_chess",
            _fields(special_activity_id="club_other", class_section_ids=("8a",), slots=(("mon", "p1"),)),
            validate=permissive_validate,
        )
    assert exc_info.value.reference_kind == "special_activity"

    reloaded = SessionFactorySchedulingProblemRepository(session_factory).load_by_school_and_year(
        problem.school.id, problem.academic_year.id,
    )
    unchanged = next(b for b in reloaded.reserved_blocks if b.id == "club_chess")
    assert unchanged.activity_id == "club_chess"
    assert unchanged.class_sections == ("8a", "8b")

    reloaded_other = SessionFactorySchedulingProblemRepository(session_factory).load_by_school_and_year(
        other_problem.school.id, other_problem.academic_year.id,
    )
    assert any(b.id == "rb_other_year" for b in reloaded_other.reserved_blocks)


def test_create_class_section_from_other_academic_year_rejected(seeded_db_two_years):
    """Representative proof for the shared nested-reference resolution
    path: `_resolve_class_sections` (and, by the identical
    `academic_year_id == year_id` pattern confirmed by direct code
    inspection, `_resolve_teacher`/`_resolve_slots`'s Day/Period
    lookups) never resolves a natural ID belonging to a different
    AcademicYear. A ClassSection that exists only in the other year is
    treated as unknown, zero mutation, even with a permissive
    `validate` callback."""
    problem, other_problem, session_factory = seeded_db_two_years
    repo = SqlAlchemyReservedActivityRepository(session_factory)

    def permissive_validate(current_problem):
        pass

    with pytest.raises(UnknownReferenceError) as exc_info:
        repo.create(
            problem.school.id, problem.academic_year.id, "rb_cross_ay_class",
            _fields(class_section_ids=("c_other",), slots=(("mon", "p1"),)),
            validate=permissive_validate,
        )
    assert exc_info.value.reference_kind == "class_section"

    remaining_ids = _reserved_block_ids(session_factory, problem.school.id, problem.academic_year.id)
    assert "rb_cross_ay_class" not in remaining_ids
