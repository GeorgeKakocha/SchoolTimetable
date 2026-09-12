"""Database-constraint tests for the Phase 3A3.1 schedule/version
persistence schema (see `docs/DECISIONS.md` #31 and
`school_timetable.persistence.models`).

Exercises PostgreSQL itself: the canonical-Schedule-per-AcademicYear
uniqueness constraint (by exact name), the active-version/parent-version
same-Schedule composite FKs, same-academic-year enforcement for every
new table, the `schedule_entry` source/ordinal constraints, the
`solver_status` CHECK, and the locked `NO ACTION` delete-action design
for the lineage/active-version edges (including the required
whole-snapshot root-cascade proof). No domain mapper/repository exists
yet (Phase 3A3.2+), so tests insert directly via the ORM models, using
the same `Session.begin_nested()` (SAVEPOINT) pattern as
`test_persistence_schema.py` so one expected failure doesn't invalidate
the rest of a test's already-flushed setup. Nothing here is ever
committed; `db_session`'s outer transaction is rolled back at teardown.
"""
from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from school_timetable.persistence import models as m
from tests_web.test_persistence_schema import _seed_year


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


def _fails(session: Session, row) -> None:
    with pytest.raises(IntegrityError):
        with session.begin_nested():
            session.add(row)
            session.flush()


def _seed_schedule_v1(session: Session, seeded: dict) -> dict:
    """Insert a canonical `Schedule` + its first `ScheduleVersion` (with
    one `ScheduleEntry`) for an already-`_seed_year`-seeded academic
    year, wiring `active_version_id` via the locked three-step sequence,
    and return every row created."""
    year = seeded["year"]
    requirement = seeded["requirement"]
    day = seeded["day"]
    period = seeded["period"]

    schedule = m.Schedule(academic_year_id=year.id, active_version_id=None)
    session.add(schedule)
    session.flush()

    version = m.ScheduleVersion(
        academic_year_id=year.id, schedule_id=schedule.id, version_number=1,
        parent_version_id=None, configuration_revision_id=seeded["revision"].id,
        solver_status="OPTIMAL", total_soft_penalty=0,
        wall_time_seconds=1.5, random_seed=None,
    )
    session.add(version)
    session.flush()

    schedule.active_version_id = version.id
    session.flush()

    entry = m.ScheduleEntry(
        academic_year_id=year.id, schedule_version_id=version.id, ordinal=0,
        source="REQUIREMENT", day_id=day.id, period_id=period.id,
        teaching_requirement_id=requirement.id, reserved_block_id=None,
    )
    session.add(entry)
    session.flush()

    return {"schedule": schedule, "version": version, "entry": entry}


def test_canonical_schedule_uniqueness_and_constraint_name(db_session):
    """A. Only one `schedule` row per `academic_year_id`, and the
    uniqueness is actually guarded by the exact constraint named
    `uq_schedule_academic_year_id` -- not merely "some" unique
    constraint."""
    seeded = _seed_year(db_session, "school-sched-a", "year-sched-a")
    year = seeded["year"]

    db_session.add(m.Schedule(academic_year_id=year.id, active_version_id=None))
    db_session.flush()

    with pytest.raises(IntegrityError) as exc_info:
        with db_session.begin_nested():
            db_session.add(m.Schedule(academic_year_id=year.id, active_version_id=None))
            db_session.flush()

    assert exc_info.value.orig.diag.constraint_name == "uq_schedule_academic_year_id"


def test_active_version_must_belong_to_same_schedule(db_session):
    """B. `schedule.active_version_id` cannot point at a
    `schedule_version` belonging to a *different* `schedule` -- the
    composite FK `(id, active_version_id) -> schedule_version
    (schedule_id, id)` must reject cross-schedule pointers."""
    seeded_a = _seed_year(db_session, "school-active-a", "year-active-a")
    seeded_b = _seed_year(db_session, "school-active-b", "year-active-b")

    built_a = _seed_schedule_v1(db_session, seeded_a)
    built_b = _seed_schedule_v1(db_session, seeded_b)

    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            built_a["schedule"].active_version_id = built_b["version"].id
            db_session.flush()


def test_version_number_uniqueness_scoped_to_schedule(db_session):
    """C. `version_number` must be unique within one `schedule`, but the
    identical `version_number` under a *different* `schedule` is
    perfectly valid (each schedule's version numbering is independent)."""
    seeded_a = _seed_year(db_session, "school-vernum-a", "year-vernum-a")
    seeded_b = _seed_year(db_session, "school-vernum-b", "year-vernum-b")
    built_a = _seed_schedule_v1(db_session, seeded_a)
    built_b = _seed_schedule_v1(db_session, seeded_b)

    # Duplicate version_number=1 within schedule A is rejected.
    _fails(db_session, m.ScheduleVersion(
        academic_year_id=seeded_a["year"].id, schedule_id=built_a["schedule"].id,
        version_number=1, parent_version_id=None, configuration_revision_id=seeded_a["revision"].id,
        solver_status="OPTIMAL", total_soft_penalty=0, wall_time_seconds=1.0, random_seed=None,
    ))

    # version_number=1 already exists for both schedule A and schedule B
    # independently (each seeded its own version 1 above) -- proving the
    # uniqueness is per-schedule, not global.
    assert built_a["version"].version_number == built_b["version"].version_number == 1
    assert built_a["schedule"].id != built_b["schedule"].id


def test_parent_version_must_belong_to_same_schedule(db_session):
    """D. `parent_version_id` must reference a version belonging to the
    *same* `schedule` -- the composite FK `(schedule_id,
    parent_version_id) -> schedule_version(schedule_id, id)` must reject
    a cross-schedule parent."""
    seeded_a = _seed_year(db_session, "school-parent-a", "year-parent-a")
    seeded_b = _seed_year(db_session, "school-parent-b", "year-parent-b")
    built_a = _seed_schedule_v1(db_session, seeded_a)
    built_b = _seed_schedule_v1(db_session, seeded_b)

    _fails(db_session, m.ScheduleVersion(
        academic_year_id=seeded_a["year"].id, schedule_id=built_a["schedule"].id,
        version_number=2, parent_version_id=built_b["version"].id,
        configuration_revision_id=seeded_a["revision"].id,
        solver_status="OPTIMAL", total_soft_penalty=0, wall_time_seconds=1.0,
        random_seed=None,
    ))


def test_cross_academic_year_references_are_rejected(db_session):
    """E. Same-academic-year enforcement for every new table, one
    isolated wrong-year field at a time (every other field correct for
    year A) so each composite FK is individually proven, not merely
    exercised alongside another wrong-year field that could mask it:
    `schedule_version` -> `schedule`; `schedule_entry` -> `schedule_version`/
    `day`/`period`/`teaching_requirement`/`reserved_block`;
    `locked_occurrence` -> `schedule_version`/`teaching_requirement`/
    `day`/`anchor_period`."""
    year_a = _seed_year(db_session, "school-cross-a", "year-cross-a")
    year_b = _seed_year(db_session, "school-cross-b", "year-cross-b")
    built_a = _seed_schedule_v1(db_session, year_a)

    reserved_b = m.ReservedBlock(
        academic_year_id=year_b["year"].id, configuration_revision_id=year_b["revision"].id,
        natural_id="club_chess_b", name="Chess Club B",
        activity_id=year_b["activity"].id, teacher_id=year_b["teacher"].id, ordinal=0,
    )
    db_session.add(reserved_b)
    db_session.flush()

    # schedule_version -> schedule (wrong year)
    _fails(db_session, m.ScheduleVersion(
        academic_year_id=year_b["year"].id, schedule_id=built_a["schedule"].id,
        version_number=1, parent_version_id=None, configuration_revision_id=year_b["revision"].id,
        solver_status="OPTIMAL", total_soft_penalty=0, wall_time_seconds=1.0, random_seed=None,
    ))

    # schedule_entry -> schedule_version (wrong year)
    _fails(db_session, m.ScheduleEntry(
        academic_year_id=year_b["year"].id, schedule_version_id=built_a["version"].id,
        ordinal=1, source="REQUIREMENT", day_id=year_b["day"].id, period_id=year_b["period"].id,
        teaching_requirement_id=year_b["requirement"].id, reserved_block_id=None,
    ))
    # schedule_entry -> day (wrong year; period/requirement correct)
    _fails(db_session, m.ScheduleEntry(
        academic_year_id=year_a["year"].id, schedule_version_id=built_a["version"].id,
        ordinal=1, source="REQUIREMENT", day_id=year_b["day"].id, period_id=year_a["period"].id,
        teaching_requirement_id=year_a["requirement"].id, reserved_block_id=None,
    ))
    # schedule_entry -> period (wrong year; day/requirement correct)
    _fails(db_session, m.ScheduleEntry(
        academic_year_id=year_a["year"].id, schedule_version_id=built_a["version"].id,
        ordinal=1, source="REQUIREMENT", day_id=year_a["day"].id, period_id=year_b["period"].id,
        teaching_requirement_id=year_a["requirement"].id, reserved_block_id=None,
    ))
    # schedule_entry -> teaching_requirement (wrong year; day/period correct)
    _fails(db_session, m.ScheduleEntry(
        academic_year_id=year_a["year"].id, schedule_version_id=built_a["version"].id,
        ordinal=1, source="REQUIREMENT", day_id=year_a["day"].id, period_id=year_a["period"].id,
        teaching_requirement_id=year_b["requirement"].id, reserved_block_id=None,
    ))
    # schedule_entry -> reserved_block (wrong year; day/period correct)
    _fails(db_session, m.ScheduleEntry(
        academic_year_id=year_a["year"].id, schedule_version_id=built_a["version"].id,
        ordinal=1, source="RESERVED_BLOCK", day_id=year_a["day"].id, period_id=year_a["period"].id,
        teaching_requirement_id=None, reserved_block_id=reserved_b.id,
    ))

    # locked_occurrence -> schedule_version (wrong year)
    _fails(db_session, m.LockedOccurrence(
        academic_year_id=year_b["year"].id, schedule_version_id=built_a["version"].id,
        teaching_requirement_id=year_b["requirement"].id, day_id=year_b["day"].id,
        anchor_period_id=year_b["period"].id,
    ))
    # locked_occurrence -> teaching_requirement (wrong year; day/anchor_period correct)
    _fails(db_session, m.LockedOccurrence(
        academic_year_id=year_a["year"].id, schedule_version_id=built_a["version"].id,
        teaching_requirement_id=year_b["requirement"].id, day_id=year_a["day"].id,
        anchor_period_id=year_a["period"].id,
    ))
    # locked_occurrence -> day (wrong year; requirement/anchor_period correct)
    _fails(db_session, m.LockedOccurrence(
        academic_year_id=year_a["year"].id, schedule_version_id=built_a["version"].id,
        teaching_requirement_id=year_a["requirement"].id, day_id=year_b["day"].id,
        anchor_period_id=year_a["period"].id,
    ))
    # locked_occurrence -> anchor_period (wrong year; requirement/day correct)
    _fails(db_session, m.LockedOccurrence(
        academic_year_id=year_a["year"].id, schedule_version_id=built_a["version"].id,
        teaching_requirement_id=year_a["requirement"].id, day_id=year_a["day"].id,
        anchor_period_id=year_b["period"].id,
    ))


def test_schedule_entry_source_check_constraints(db_session):
    """F. `source` must be exactly `REQUIREMENT`/`RESERVED_BLOCK`, and
    exactly the matching one of `teaching_requirement_id`/
    `reserved_block_id` must be populated -- both populated, neither
    populated, a source/reference mismatch, and invalid source text are
    all rejected; the two valid combinations are accepted."""
    seeded = _seed_year(db_session, "school-source-check", "year-source-check")
    year, day, period, requirement = seeded["year"], seeded["day"], seeded["period"], seeded["requirement"]

    reserved = m.ReservedBlock(
        academic_year_id=year.id, configuration_revision_id=seeded["revision"].id,
        natural_id="club_chess", name="Chess Club",
        activity_id=seeded["activity"].id, teacher_id=seeded["teacher"].id, ordinal=0,
    )
    db_session.add(reserved)
    db_session.flush()

    built = _seed_schedule_v1(db_session, seeded)
    version = built["version"]

    def entry(ordinal, **kwargs):
        base = dict(
            academic_year_id=year.id, schedule_version_id=version.id, ordinal=ordinal,
            day_id=day.id, period_id=period.id, teaching_requirement_id=None, reserved_block_id=None,
        )
        base.update(kwargs)
        return m.ScheduleEntry(**base)

    # Valid: RESERVED_BLOCK with only reserved_block_id populated.
    db_session.add(entry(1, source="RESERVED_BLOCK", reserved_block_id=reserved.id))
    db_session.flush()

    # Both populated -> rejected.
    _fails(db_session, entry(
        2, source="REQUIREMENT", teaching_requirement_id=requirement.id, reserved_block_id=reserved.id,
    ))
    # Neither populated -> rejected.
    _fails(db_session, entry(3, source="REQUIREMENT"))
    # Source/reference mismatch (REQUIREMENT but reserved_block_id set) -> rejected.
    _fails(db_session, entry(4, source="REQUIREMENT", reserved_block_id=reserved.id))
    # Invalid source text -> rejected.
    _fails(db_session, entry(5, source="SOMETHING_ELSE", teaching_requirement_id=requirement.id))


def test_schedule_entry_ordinal_uniqueness(db_session):
    """G. `ordinal` must be unique within one `schedule_version` --
    duplicate `ordinal` is rejected -- and reading back by `ORDER BY
    ordinal` reconstructs the exact insertion order, proving the
    deterministic-ordering infrastructure actually works."""
    seeded = _seed_year(db_session, "school-ordinal", "year-ordinal")
    built = _seed_schedule_v1(db_session, seeded)
    version = built["version"]
    day, period, requirement = seeded["day"], seeded["period"], seeded["requirement"]

    # built["entry"] already occupies ordinal=0. Adding a second at the
    # same ordinal is rejected.
    _fails(db_session, m.ScheduleEntry(
        academic_year_id=seeded["year"].id, schedule_version_id=version.id, ordinal=0,
        source="REQUIREMENT", day_id=day.id, period_id=period.id,
        teaching_requirement_id=requirement.id, reserved_block_id=None,
    ))

    # A second entry at a distinct ordinal succeeds and round-trips in order.
    db_session.add(m.ScheduleEntry(
        academic_year_id=seeded["year"].id, schedule_version_id=version.id, ordinal=1,
        source="REQUIREMENT", day_id=day.id, period_id=period.id,
        teaching_requirement_id=requirement.id, reserved_block_id=None,
    ))
    db_session.flush()

    rows = (
        db_session.query(m.ScheduleEntry)
        .filter_by(schedule_version_id=version.id)
        .order_by(m.ScheduleEntry.ordinal)
        .all()
    )
    assert [r.ordinal for r in rows] == [0, 1]


@pytest.mark.parametrize("solver_status", ["OPTIMAL", "FEASIBLE"])
def test_schedule_version_solver_status_accepts_valid_values(db_session, solver_status):
    """H (accepted values). Only `OPTIMAL`/`FEASIBLE` may ever be
    persisted as a successful `ScheduleVersion`."""
    seeded = _seed_year(db_session, f"school-status-{solver_status.lower()}", f"year-status-{solver_status.lower()}")
    schedule = m.Schedule(academic_year_id=seeded["year"].id, active_version_id=None)
    db_session.add(schedule)
    db_session.flush()

    db_session.add(m.ScheduleVersion(
        academic_year_id=seeded["year"].id, schedule_id=schedule.id, version_number=1,
        parent_version_id=None, configuration_revision_id=seeded["revision"].id,
        solver_status=solver_status, total_soft_penalty=0,
        wall_time_seconds=1.0, random_seed=None,
    ))
    db_session.flush()


@pytest.mark.parametrize("solver_status", ["INVALID_INPUT", "INFEASIBLE", "ERROR", "SOMETHING_ELSE"])
def test_schedule_version_solver_status_rejects_non_persistable_values(db_session, solver_status):
    """H (rejected values). `INVALID_INPUT`/`INFEASIBLE`/`ERROR` (real
    `SolverStatus` values that must never reach persistence) and
    arbitrary invalid text are all rejected by the CHECK constraint."""
    seeded = _seed_year(db_session, f"school-bad-status-{solver_status.lower()}", f"year-bad-status-{solver_status.lower()}")
    schedule = m.Schedule(academic_year_id=seeded["year"].id, active_version_id=None)
    db_session.add(schedule)
    db_session.flush()

    _fails(db_session, m.ScheduleVersion(
        academic_year_id=seeded["year"].id, schedule_id=schedule.id, version_number=1,
        parent_version_id=None, configuration_revision_id=seeded["revision"].id,
        solver_status=solver_status, total_soft_penalty=0,
        wall_time_seconds=1.0, random_seed=None,
    ))


def test_direct_delete_of_active_or_referenced_parent_version_rejected(db_session):
    """I.1, I.2. A direct delete of the current active `schedule_version`
    is rejected; a direct delete of a `schedule_version` still referenced
    as another version's `parent_version_id` is rejected. Both via
    `NO ACTION`, per Decision #31's locked lineage/active-version delete
    design (not `RESTRICT`, but behaving identically for an isolated
    direct delete)."""
    seeded = _seed_year(db_session, "school-direct-delete", "year-direct-delete")
    built = _seed_schedule_v1(db_session, seeded)

    # I.1: version 1 is the active version -- direct delete rejected.
    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.delete(built["version"])
            db_session.flush()

    # I.2: create version 2 as a child of version 1, repoint active to
    # version 2, then attempt to delete version 1 (now a referenced
    # parent, no longer active) -- still rejected.
    version2 = m.ScheduleVersion(
        academic_year_id=seeded["year"].id, schedule_id=built["schedule"].id, version_number=2,
        parent_version_id=built["version"].id, configuration_revision_id=seeded["revision"].id,
        solver_status="OPTIMAL", total_soft_penalty=0,
        wall_time_seconds=2.0, random_seed=None,
    )
    db_session.add(version2)
    db_session.flush()
    built["schedule"].active_version_id = version2.id
    db_session.flush()

    with pytest.raises(IntegrityError):
        with db_session.begin_nested():
            db_session.delete(built["version"])
            db_session.flush()


def test_academic_year_root_delete_cascades_entire_schedule_graph(db_session):
    """I.3, J. Deleting the `academic_year` root successfully removes
    the entire `schedule`/`schedule_version`/`schedule_entry`/
    `locked_occurrence` graph in one statement -- proving the `NO
    ACTION` lineage/active-version edges do not block the whole-snapshot
    root cascade Decision #31 requires. No orphan rows survive."""
    seeded = _seed_year(db_session, "school-root-cascade", "year-root-cascade")
    year = seeded["year"]
    built = _seed_schedule_v1(db_session, seeded)

    db_session.add(m.LockedOccurrence(
        academic_year_id=year.id, schedule_version_id=built["version"].id,
        teaching_requirement_id=seeded["requirement"].id, day_id=seeded["day"].id,
        anchor_period_id=seeded["period"].id,
    ))
    db_session.flush()

    schedule_id = built["schedule"].id
    version_id = built["version"].id

    db_session.delete(year)
    db_session.flush()

    assert db_session.query(m.Schedule).filter_by(id=schedule_id).count() == 0
    assert db_session.query(m.ScheduleVersion).filter_by(id=version_id).count() == 0
    assert db_session.query(m.ScheduleEntry).filter_by(schedule_version_id=version_id).count() == 0
    assert db_session.query(m.LockedOccurrence).filter_by(schedule_version_id=version_id).count() == 0
