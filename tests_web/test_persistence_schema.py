"""Database-constraint tests for the Phase 3A2.1 persisted
scheduling-configuration schema (see `docs/DECISIONS.md` #26 and
`school_timetable.persistence.models`).

These exercise PostgreSQL itself -- composite-FK same-academic-year
isolation, CASCADE/RESTRICT delete semantics, CHECK constraints, and
ordinal/natural-ID uniqueness -- not application code. No domain
mapper/repository exists yet (Phase 3A2.2+), so tests insert directly
via the ORM models, using `Session.begin_nested()` (a SAVEPOINT) around
each expected-failure insert so one failing assertion doesn't invalidate
the rest of the test's already-flushed setup rows. Nothing here is ever
committed; `db_session`'s outer transaction is rolled back at teardown.
"""
from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from school_timetable.persistence import models as m


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


def _seed_year(session: Session, school_natural: str, year_natural: str) -> dict:
    """Insert one minimal, fully cross-linked academic-year configuration
    and return every row so tests can build further inserts on top of it."""
    school = m.School(natural_id=school_natural, name=f"School {school_natural}")
    session.add(school)
    session.flush()

    year = m.AcademicYear(school_id=school.id, natural_id=year_natural, label=year_natural)
    session.add(year)
    session.flush()

    day = m.Day(academic_year_id=year.id, natural_id="mon", name="Monday", idx=0)
    period = m.Period(academic_year_id=year.id, natural_id="p1", name="Period 1", idx=0, block_id="morning")
    class_section = m.ClassSection(academic_year_id=year.id, natural_id="8a", name="8-A", ordinal=0)
    teacher = m.Teacher(academic_year_id=year.id, natural_id="t_math", name="Teacher Math", ordinal=0)
    activity = m.Activity(academic_year_id=year.id, natural_id="math", name="Math", ordinal=0)
    resource = m.Resource(academic_year_id=year.id, natural_id="gym", name="Gym", capacity=1, ordinal=0)
    session.add_all([day, period, class_section, teacher, activity, resource])
    session.flush()

    group = m.ParticipantGroup(academic_year_id=year.id, natural_id="pg_8a", name="All 8-A", ordinal=0)
    session.add(group)
    session.flush()

    membership = m.ParticipantGroupClassSection(
        academic_year_id=year.id, participant_group_id=group.id, class_section_id=class_section.id, ordinal=0,
    )
    session.add(membership)

    requirement = m.TeachingRequirement(
        academic_year_id=year.id, natural_id="math_8a", teacher_id=teacher.id, activity_id=activity.id,
        participant_group_id=group.id, weekly_periods=5, ordinal=0,
    )
    session.add(requirement)
    session.flush()

    return {
        "school": school, "year": year, "day": day, "period": period,
        "class_section": class_section, "teacher": teacher, "activity": activity,
        "resource": resource, "group": group, "requirement": requirement,
    }


def _fails(session: Session, row) -> None:
    """Add `row` inside a SAVEPOINT and assert it is rejected by
    PostgreSQL, leaving `session` usable for further assertions."""
    with pytest.raises(IntegrityError):
        with session.begin_nested():
            session.add(row)
            session.flush()


def test_same_year_full_config_inserts_cleanly(db_session):
    """Every locked entity/child table accepts a valid, fully-linked,
    same-academic-year row -- the positive control for every other test
    here, which only exercises the negative/rejection paths."""
    seeded = _seed_year(db_session, "school-ok", "year-ok")
    year, teacher, day, period = seeded["year"], seeded["teacher"], seeded["day"], seeded["period"]
    requirement = seeded["requirement"]

    db_session.add(m.TeacherAvailability(
        academic_year_id=year.id, teacher_id=teacher.id, day_id=day.id, period_id=period.id,
        status="UNAVAILABLE", ordinal=0,
    ))
    db_session.add(m.TimePreference(
        academic_year_id=year.id, teaching_requirement_id=requirement.id, ordinal=0,
        preferred_period_indexes=[0, 1], weight="HIGH",
    ))
    db_session.add(m.FixedPlacement(
        academic_year_id=year.id, natural_id="fixed_math_8a", teaching_requirement_id=requirement.id,
        day_id=day.id, period_id=period.id, ordinal=0,
    ))
    reserved = m.ReservedBlock(
        academic_year_id=year.id, natural_id="club_chess", name="Chess Club",
        activity_id=seeded["activity"].id, teacher_id=teacher.id, ordinal=0,
    )
    db_session.add(reserved)
    db_session.flush()
    db_session.add(m.ReservedBlockClassSection(
        academic_year_id=year.id, reserved_block_id=reserved.id,
        class_section_id=seeded["class_section"].id, ordinal=0,
    ))
    db_session.add(m.ReservedBlockSlot(
        academic_year_id=year.id, reserved_block_id=reserved.id, day_id=day.id, period_id=period.id, ordinal=0,
    ))
    db_session.flush()  # no IntegrityError anywhere above


def test_cross_academic_year_references_are_rejected(db_session):
    """A row claiming one academic_year_id must never be able to
    reference a sibling entity that actually belongs to a different
    academic_year_id -- proven at the PostgreSQL level, not in Python."""
    year_a = _seed_year(db_session, "school-a", "year-a")
    year_b = _seed_year(db_session, "school-b", "year-b")

    # teaching_requirement -> teacher across years
    _fails(db_session, m.TeachingRequirement(
        academic_year_id=year_a["year"].id, natural_id="cross_teacher", teacher_id=year_b["teacher"].id,
        activity_id=year_a["activity"].id, participant_group_id=year_a["group"].id,
        weekly_periods=1, ordinal=1,
    ))

    # teacher_availability -> day and -> period across years
    _fails(db_session, m.TeacherAvailability(
        academic_year_id=year_a["year"].id, teacher_id=year_a["teacher"].id,
        day_id=year_b["day"].id, period_id=year_a["period"].id, status="AVAILABLE", ordinal=1,
    ))
    _fails(db_session, m.TeacherAvailability(
        academic_year_id=year_a["year"].id, teacher_id=year_a["teacher"].id,
        day_id=year_a["day"].id, period_id=year_b["period"].id, status="AVAILABLE", ordinal=1,
    ))

    # participant_group_class_section -> class_section across years
    _fails(db_session, m.ParticipantGroupClassSection(
        academic_year_id=year_a["year"].id, participant_group_id=year_a["group"].id,
        class_section_id=year_b["class_section"].id, ordinal=1,
    ))

    # fixed_placement -> teaching_requirement across years
    _fails(db_session, m.FixedPlacement(
        academic_year_id=year_a["year"].id, natural_id="cross_fp", teaching_requirement_id=year_b["requirement"].id,
        day_id=year_a["day"].id, period_id=year_a["period"].id, ordinal=1,
    ))


def test_natural_id_uniqueness_is_per_academic_year(db_session):
    """A duplicate `natural_id` within one academic year is rejected; the
    identical `natural_id` in a different academic year is allowed --
    natural IDs are only unique within their own configuration snapshot."""
    year_a = _seed_year(db_session, "school-dup-a", "year-dup-a")
    year_b = _seed_year(db_session, "school-dup-b", "year-dup-b")

    _fails(db_session, m.Teacher(
        academic_year_id=year_a["year"].id, natural_id="t_math", name="Duplicate Teacher Math", ordinal=1,
    ))

    # Same natural_id, different academic year: allowed (already inserted
    # by _seed_year for both years above with no error raised).
    assert year_a["teacher"].natural_id == year_b["teacher"].natural_id == "t_math"
    assert year_a["teacher"].id != year_b["teacher"].id


def test_day_and_period_idx_uniqueness_within_academic_year(db_session):
    seeded = _seed_year(db_session, "school-idx", "year-idx")
    year = seeded["year"]

    _fails(db_session, m.Day(academic_year_id=year.id, natural_id="tue", name="Tuesday", idx=0))
    _fails(db_session, m.Period(
        academic_year_id=year.id, natural_id="p2", name="Period 2", idx=0, block_id="morning",
    ))


def test_enum_check_constraints_reject_invalid_values(db_session):
    seeded = _seed_year(db_session, "school-enum", "year-enum")
    year, teacher, day, period = seeded["year"], seeded["teacher"], seeded["day"], seeded["period"]

    _fails(db_session, m.TeacherAvailability(
        academic_year_id=year.id, teacher_id=teacher.id, day_id=day.id, period_id=period.id,
        status="SOMETIMES", ordinal=1,
    ))
    _fails(db_session, m.Activity(
        academic_year_id=year.id, natural_id="mystery", name="Mystery", kind="UNKNOWN_KIND", ordinal=1,
    ))
    _fails(db_session, m.TeachingRequirement(
        academic_year_id=year.id, natural_id="bad_mode", teacher_id=teacher.id, activity_id=seeded["activity"].id,
        participant_group_id=seeded["group"].id, weekly_periods=1, block_mode="SOMETIMES", ordinal=1,
    ))
    _fails(db_session, m.TimePreference(
        academic_year_id=year.id, teaching_requirement_id=seeded["requirement"].id, ordinal=1,
        preferred_period_indexes=[0], weight="EXTREME",
    ))


def test_positive_value_check_constraints(db_session):
    seeded = _seed_year(db_session, "school-positive", "year-positive")
    year = seeded["year"]

    _fails(db_session, m.TeachingRequirement(
        academic_year_id=year.id, natural_id="zero_periods", teacher_id=seeded["teacher"].id,
        activity_id=seeded["activity"].id, participant_group_id=seeded["group"].id,
        weekly_periods=0, ordinal=1,
    ))
    _fails(db_session, m.Resource(
        academic_year_id=year.id, natural_id="bad_resource", name="Bad Resource", capacity=0, ordinal=1,
    ))


def test_owned_child_cascade_delete(db_session):
    """Deleting a true-owning parent (teacher) removes its owned child
    row (teacher_availability) automatically -- Decision B."""
    seeded = _seed_year(db_session, "school-cascade", "year-cascade")
    teacher, day, period, year = seeded["teacher"], seeded["day"], seeded["period"], seeded["year"]

    db_session.add(m.TeacherAvailability(
        academic_year_id=year.id, teacher_id=teacher.id, day_id=day.id, period_id=period.id,
        status="UNAVAILABLE", ordinal=0,
    ))
    db_session.flush()

    # Delete the teaching_requirement first (it RESTRICTs teacher
    # deletion too) so this test isolates the CASCADE edge only.
    db_session.delete(seeded["requirement"])
    db_session.flush()

    db_session.delete(teacher)
    db_session.flush()

    remaining = db_session.query(m.TeacherAvailability).filter_by(teacher_id=teacher.id).count()
    assert remaining == 0


def test_cross_entity_restrict_blocks_sibling_delete(db_session):
    """A cross-entity RESTRICT edge must block deleting the referenced
    row while a sibling still points at it -- no silent cascade, no
    silent SET NULL, for either a required or an optional reference."""
    seeded = _seed_year(db_session, "school-restrict", "year-restrict")
    teacher, resource, year = seeded["teacher"], seeded["resource"], seeded["year"]

    # Required reference: teaching_requirement -> teacher (RESTRICT)
    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.delete(teacher)
            db_session.flush()

    # Optional reference: teaching_requirement.resource_id -> resource
    # (RESTRICT, never SET NULL) -- attach the optional resource first.
    seeded["requirement"].resource_id = resource.id
    db_session.flush()

    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.delete(resource)
            db_session.flush()

    # The requirement's optional resource_id must still be exactly what
    # we set -- proving the failed delete did not silently null it out.
    db_session.refresh(seeded["requirement"])
    assert seeded["requirement"].resource_id == resource.id


def test_ordinal_and_membership_uniqueness_on_ordered_child_tables(db_session):
    seeded = _seed_year(db_session, "school-ordinal", "year-ordinal")
    year, group, class_section = seeded["year"], seeded["group"], seeded["class_section"]

    other_class = m.ClassSection(academic_year_id=year.id, natural_id="8b", name="8-B", ordinal=1)
    db_session.add(other_class)
    db_session.flush()

    # Same (participant_group_id, ordinal) twice -> PK violation.
    _fails(db_session, m.ParticipantGroupClassSection(
        academic_year_id=year.id, participant_group_id=group.id, class_section_id=other_class.id, ordinal=0,
    ))

    # Same (participant_group_id, class_section_id) twice at a different
    # ordinal -> duplicate-membership violation.
    _fails(db_session, m.ParticipantGroupClassSection(
        academic_year_id=year.id, participant_group_id=group.id, class_section_id=class_section.id, ordinal=1,
    ))


def test_deleting_academic_year_cascades_complete_snapshot(db_session):
    """Deleting the root `AcademicYear` must remove every row scoped
    under it -- the behavioral proof of Decision A ("every directly
    scoped configuration row's academic_year_id is ON DELETE CASCADE"),
    even though the same graph is riddled with cross-entity RESTRICT
    edges (Decision C) between the tables being cascaded away together.
    `School` must survive untouched."""
    seeded = _seed_year(db_session, "school-root-delete", "year-root-delete")
    year = seeded["year"]
    school, teacher, day, period = seeded["school"], seeded["teacher"], seeded["day"], seeded["period"]
    activity, resource, group = seeded["activity"], seeded["resource"], seeded["group"]
    requirement, class_section = seeded["requirement"], seeded["class_section"]

    # Exercise the RESTRICT edges, not just independent rows: attach the
    # optional resource to the requirement, add a TimePreference, a
    # TeacherAvailability, a FixedPlacement, and a ReservedBlock that
    # itself references activity/teacher and has its own class-section
    # and slot child rows.
    requirement.resource_id = resource.id
    db_session.add(m.TimePreference(
        academic_year_id=year.id, teaching_requirement_id=requirement.id, ordinal=0,
        preferred_period_indexes=[0, 1], weight="HIGH",
    ))
    db_session.add(m.TeacherAvailability(
        academic_year_id=year.id, teacher_id=teacher.id, day_id=day.id, period_id=period.id,
        status="UNAVAILABLE", ordinal=0,
    ))
    db_session.add(m.FixedPlacement(
        academic_year_id=year.id, natural_id="fixed_math_8a", teaching_requirement_id=requirement.id,
        day_id=day.id, period_id=period.id, ordinal=0,
    ))
    reserved = m.ReservedBlock(
        academic_year_id=year.id, natural_id="club_chess", name="Chess Club",
        activity_id=activity.id, teacher_id=teacher.id, ordinal=0,
    )
    db_session.add(reserved)
    db_session.flush()
    db_session.add(m.ReservedBlockClassSection(
        academic_year_id=year.id, reserved_block_id=reserved.id, class_section_id=class_section.id, ordinal=0,
    ))
    db_session.add(m.ReservedBlockSlot(
        academic_year_id=year.id, reserved_block_id=reserved.id, day_id=day.id, period_id=period.id, ordinal=0,
    ))
    db_session.flush()

    school_id = school.id
    year_id = year.id

    scoped_tables = [
        m.Day, m.Period, m.ClassSection, m.ParticipantGroup, m.Teacher, m.Activity,
        m.Resource, m.TeachingRequirement, m.ReservedBlock, m.FixedPlacement,
    ]
    for table in scoped_tables:
        assert db_session.query(table).filter_by(academic_year_id=year_id).count() > 0

    db_session.delete(year)
    db_session.flush()

    for table in scoped_tables:
        assert db_session.query(table).filter_by(academic_year_id=year_id).count() == 0
    assert db_session.query(m.TeacherAvailability).filter_by(academic_year_id=year_id).count() == 0
    assert db_session.query(m.TimePreference).filter_by(academic_year_id=year_id).count() == 0
    assert db_session.query(m.ParticipantGroupClassSection).filter_by(academic_year_id=year_id).count() == 0
    assert db_session.query(m.ReservedBlockClassSection).filter_by(academic_year_id=year_id).count() == 0
    assert db_session.query(m.ReservedBlockSlot).filter_by(academic_year_id=year_id).count() == 0

    assert db_session.query(m.AcademicYear).filter_by(id=year_id).count() == 0
    assert db_session.query(m.School).filter_by(id=school_id).count() == 1
