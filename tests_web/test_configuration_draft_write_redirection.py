"""Real-PostgreSQL HTTP integration test for Safe Configuration
Changes, Slice B's core cross-layer promise: once a configuration draft
is open (`POST .../configuration/draft`), an ordinary configuration
write is redirected to that draft revision -- never to the published
one -- and a normal read/projection becomes draft-first; discarding the
draft (`DELETE .../configuration/draft`) restores the published view
exactly, with the published revision's own rows never mutated at any
point.

Uses Teacher (`TeacherService`/`TeacherProjectionService`) as the one
representative configuration resource: a real, server-generated
natural ID, real persisted relationships-free row (simplest possible
write to isolate the draft-redirection behavior from unrelated
validation rules), and an existing read projection
(`GET .../teachers`) that already goes through the same draft-first
`SchedulingProblemRepository.load_by_school_and_year` every other
Setup screen uses.

Does not re-prove the exhaustive 15-table/FK eager-clone correctness
(`tests_web/test_configuration_revision_repository.py`) or the
configuration-revision routes' own contract
(`tests_web/test_configuration_revision_api.py`) -- this file proves
only the write-redirection/read-precedence promise itself.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from school_timetable.api.dependencies import (
    get_configuration_revision_repository,
    get_generate_schedule_service,
    get_teacher_service,
    get_teachers_projection_service,
)
from school_timetable.api.main import app
from school_timetable.application.generate_schedule_service import GenerateScheduleService
from school_timetable.application.teacher_projection_service import TeacherProjectionService
from school_timetable.application.teacher_service import TeacherService
from school_timetable.domain.problem import SchedulingProblem
from school_timetable.fixtures.valid_fixture import build_valid_fixture
from school_timetable.persistence import models as m
from school_timetable.persistence.configuration_revision_repository import (
    SqlAlchemyConfigurationRevisionRepository,
)
from school_timetable.persistence.problem_repository import SessionFactorySchedulingProblemRepository
from school_timetable.persistence.schedule_repository import SqlAlchemyScheduleVersionRepository
from school_timetable.persistence.teacher_repository import SqlAlchemyTeacherRepository
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


def _client(session_factory) -> TestClient:
    def override_configuration_revision_repository():
        return SqlAlchemyConfigurationRevisionRepository(session_factory)

    def override_generate_service():
        return GenerateScheduleService(
            SessionFactorySchedulingProblemRepository(session_factory),
            SqlAlchemyScheduleVersionRepository(session_factory),
        )

    def override_teachers_projection_service():
        return TeacherProjectionService(
            SessionFactorySchedulingProblemRepository(session_factory),
            SqlAlchemyConfigurationRevisionRepository(session_factory),
        )

    def override_teacher_service():
        return TeacherService(
            SessionFactorySchedulingProblemRepository(session_factory),
            SqlAlchemyTeacherRepository(session_factory),
        )

    app.dependency_overrides[get_configuration_revision_repository] = override_configuration_revision_repository
    app.dependency_overrides[get_generate_schedule_service] = override_generate_service
    app.dependency_overrides[get_teachers_projection_service] = override_teachers_projection_service
    app.dependency_overrides[get_teacher_service] = override_teacher_service
    return TestClient(app)


@pytest.fixture
def client(db):
    _session, session_factory = db
    try:
        yield _client(session_factory)
    finally:
        app.dependency_overrides.pop(get_configuration_revision_repository, None)
        app.dependency_overrides.pop(get_generate_schedule_service, None)
        app.dependency_overrides.pop(get_teachers_projection_service, None)
        app.dependency_overrides.pop(get_teacher_service, None)


def _seed(session: Session) -> SchedulingProblem:
    problem = build_valid_fixture()
    write_scheduling_problem(session, problem)
    session.flush()
    return problem


def _generate(client: TestClient, problem: SchedulingProblem):
    response = client.post(f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/generate")
    assert response.status_code == 201
    return response


def _teachers_url(problem: SchedulingProblem, *parts: str) -> str:
    return "/".join((f"/schools/{problem.school.id}/years/{problem.academic_year.id}/teachers", *parts))


def _draft_url(problem: SchedulingProblem) -> str:
    return f"/schools/{problem.school.id}/years/{problem.academic_year.id}/configuration/draft"


def _year_row(session: Session, academic_year_natural_id: str) -> m.AcademicYear:
    return session.execute(
        select(m.AcademicYear).where(m.AcademicYear.natural_id == academic_year_natural_id)
    ).scalar_one()


def _teacher_snapshot(session: Session, year_id: int, revision_id: int) -> frozenset[tuple]:
    rows = session.execute(
        select(m.Teacher).where(m.Teacher.academic_year_id == year_id, m.Teacher.configuration_revision_id == revision_id)
    ).scalars().all()
    return frozenset((r.natural_id, r.first_name, r.last_name, r.ordinal) for r in rows)


# == complementary locked behavior ============================================

def test_teacher_create_rejected_while_locked_before_any_draft_is_open(client, db):
    session, _session_factory = db
    problem = _seed(session)
    _generate(client, problem)

    response = client.post(_teachers_url(problem), json={"first_name": "Nino", "last_name": "Beridze"})

    assert response.status_code == 409
    assert response.json()["code"] == "SCHEDULING_CONFIGURATION_LOCKED"

    year_id = _year_row(session, problem.academic_year.id).id
    published_id = _year_row(session, problem.academic_year.id).published_revision_id
    # No new teacher was created anywhere.
    assert len(_teacher_snapshot(session, year_id, published_id)) == len(problem.teachers)


# == the core cross-layer promise =============================================

def test_teacher_write_redirects_to_draft_leaves_published_untouched_and_discard_restores_view(client, db):
    session, session_factory = db
    problem = _seed(session)
    _generate(client, problem)

    year_id = _year_row(session, problem.academic_year.id).id
    published_id = _year_row(session, problem.academic_year.id).published_revision_id
    published_snapshot_before = _teacher_snapshot(session, year_id, published_id)

    # (8, pre-draft baseline) the published-state read, for later
    # comparison after discard.
    published_read = client.get(_teachers_url(problem)).json()
    assert published_read["configuration_locked"] is True
    published_teacher_ids_before = {t["id"] for t in published_read["teachers"]}

    # (3) Open a draft through the real HTTP endpoint.
    begin_response = client.post(_draft_url(problem))
    assert begin_response.status_code == 200
    draft_id = _year_row(session, problem.academic_year.id).draft_revision_id
    assert draft_id is not None and draft_id != published_id

    # (4/5) A real configuration write through the existing HTTP API,
    # now that a draft is open -- must succeed (previously rejected
    # with SCHEDULING_CONFIGURATION_LOCKED, proven above).
    create_response = client.post(_teachers_url(problem), json={"first_name": "Nino", "last_name": "Beridze"})
    assert create_response.status_code == 201
    new_teacher_id = create_response.json()["id"]

    # (6) The new row belongs to the DRAFT revision, never the published one.
    new_row = session.execute(
        select(m.Teacher).where(m.Teacher.academic_year_id == year_id, m.Teacher.natural_id == new_teacher_id)
    ).scalar_one()
    assert new_row.configuration_revision_id == draft_id
    assert new_row.configuration_revision_id != published_id

    # (7) The published revision's Teacher rows are completely
    # unchanged -- same snapshot, byte-for-byte, as before the draft
    # was ever opened or written to.
    assert _teacher_snapshot(session, year_id, published_id) == published_snapshot_before

    # (8) A normal read/projection now returns the draft-first result:
    # the new teacher IS visible, and configuration is no longer locked.
    draft_read = client.get(_teachers_url(problem)).json()
    assert draft_read["configuration_locked"] is False
    draft_teacher_ids = {t["id"] for t in draft_read["teachers"]}
    assert new_teacher_id in draft_teacher_ids
    assert draft_teacher_ids == published_teacher_ids_before | {new_teacher_id}

    # (9) Discard the draft through the real HTTP endpoint.
    discard_response = client.delete(_draft_url(problem))
    assert discard_response.status_code == 200
    assert discard_response.json()["configuration_locked"] is True

    # (10) The same read/projection returns the published state again
    # -- the draft-only new teacher is gone, exactly the original list.
    after_discard_read = client.get(_teachers_url(problem)).json()
    assert after_discard_read["configuration_locked"] is True
    after_discard_teacher_ids = {t["id"] for t in after_discard_read["teachers"]}
    assert after_discard_teacher_ids == published_teacher_ids_before
    assert new_teacher_id not in after_discard_teacher_ids

    # (11) The published rows are still exactly unchanged after discard.
    assert _teacher_snapshot(session, year_id, published_id) == published_snapshot_before

    # The draft revision (and the teacher created inside it) is
    # actually gone, not merely unreferenced.
    assert session.get(m.ConfigurationRevision, draft_id) is None
    assert session.execute(
        select(m.Teacher).where(m.Teacher.academic_year_id == year_id, m.Teacher.natural_id == new_teacher_id)
    ).scalar_one_or_none() is None
