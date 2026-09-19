from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from school_timetable.application.errors import ConfigurationLockedError
from school_timetable.application.synchronized_split_models import (
    CreateSynchronizedSplitCommand, SynchronizedSplitBranchFields, SynchronizedSplitPublicIds,
)
from school_timetable.application.synchronized_split_rules import validate_create
from school_timetable.fixtures.valid_fixture import build_valid_fixture
from school_timetable.persistence import models as m
from school_timetable.persistence.problem_repository import SessionFactorySchedulingProblemRepository
from school_timetable.persistence.synchronized_split_repository import SqlAlchemySynchronizedSplitRepository
from tests_web.support.problem_writer import write_scheduling_problem


@pytest.fixture
def seeded_split_db(live_db_engine):
    problem = build_valid_fixture()
    with Session(live_db_engine) as session:
        write_scheduling_problem(session, problem); session.commit()
    factory = sessionmaker(bind=live_db_engine, autoflush=False, expire_on_commit=False)
    try:
        yield problem, factory
    finally:
        with Session(live_db_engine) as session:
            school = session.execute(
                select(m.School).where(m.School.natural_id == problem.school.id)
            ).scalar_one_or_none()
            if school is not None: session.delete(school); session.commit()


def _command(problem):
    return CreateSynchronizedSplitCommand(
        problem.school.id, problem.academic_year.id, "9a", 2,
        SynchronizedSplitBranchFields("Russian subgroup", "t_russian", "russian"),
        SynchronizedSplitBranchFields("German subgroup", "t_german", "german"),
    )


def _ids(second_requirement="req_split_b"):
    return SynchronizedSplitPublicIds(
        "split_acceptance", "group_split_a", "group_split_b", "req_split_a", second_requirement,
    )


def _validate(command, ids):
    return lambda problem: validate_create(problem, command, ids)


def test_atomic_create_round_trip(seeded_split_db):
    problem, factory = seeded_split_db; command = _command(problem); ids = _ids()
    SqlAlchemySynchronizedSplitRepository(factory).create(command, ids, _validate(command, ids))
    reloaded = SessionFactorySchedulingProblemRepository(factory).load_by_school_and_year(
        problem.school.id, problem.academic_year.id,
    )
    groups = [g for g in reloaded.participant_groups if g.id in {ids.branch_a_group_id, ids.branch_b_group_id}]
    requirements = [
        r for r in reloaded.teaching_requirements
        if r.id in {ids.branch_a_requirement_id, ids.branch_b_requirement_id}
    ]
    assert len(groups) == 2 and all(g.role.value == "SUBGROUP" and g.class_sections == ("9a",) for g in groups)
    assert len(requirements) == 2
    assert {r.split_group_id for r in requirements} == {ids.split_group_id}
    assert {r.weekly_periods for r in requirements} == {2}
    assert {(r.teacher_id, r.activity_id, r.participant_group_id) for r in requirements} == {
        ("t_russian", "russian", ids.branch_a_group_id),
        ("t_german", "german", ids.branch_b_group_id),
    }
    assert all(r.distribution_policy.max_periods_per_day is None and r.distribution_policy.min_distinct_days is None
               for r in requirements)


def test_second_requirement_failure_rolls_back_groups_memberships_and_first_requirement(seeded_split_db):
    problem, factory = seeded_split_db; command = _command(problem)
    # Duplicate requirement IDs fail on the second row at flush/commit,
    # after both groups and memberships exist in-session.
    ids = _ids(second_requirement="req_split_a")
    with pytest.raises(Exception):
        SqlAlchemySynchronizedSplitRepository(factory).create(command, ids, lambda problem: None)
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(m.ParticipantGroup).where(
            m.ParticipantGroup.natural_id.in_([ids.branch_a_group_id, ids.branch_b_group_id]))) == 0
        assert session.scalar(select(func.count()).select_from(m.TeachingRequirement).where(
            m.TeachingRequirement.natural_id == ids.branch_a_requirement_id)) == 0


def test_locked_configuration_rejected_without_rows(seeded_split_db):
    problem, factory = seeded_split_db; command = _command(problem); ids = _ids()
    with factory() as session:
        year = session.execute(
            select(m.AcademicYear).where(m.AcademicYear.natural_id == problem.academic_year.id)
        ).scalar_one()
        year.draft_revision_id = None; session.commit()
    with pytest.raises(ConfigurationLockedError):
        SqlAlchemySynchronizedSplitRepository(factory).create(command, ids, _validate(command, ids))
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(m.ParticipantGroup).where(
            m.ParticipantGroup.natural_id.in_([ids.branch_a_group_id, ids.branch_b_group_id]))) == 0


def test_authoritative_validation_reloads_under_lock(seeded_split_db):
    problem, factory = seeded_split_db; command = _command(problem); ids = _ids(); seen = []
    def validate(current):
        seen.append(current)
        assert any(c.id == "9a" for c in current.class_sections)
    SqlAlchemySynchronizedSplitRepository(factory).create(command, ids, validate)
    assert len(seen) == 1
