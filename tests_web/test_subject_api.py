"""Real-PostgreSQL HTTP integration tests for Real-School Setup MVP
Slice D's `GET/POST .../subjects` and `PUT/DELETE .../subjects/{subject_id}`.

Uses the same `join_transaction_mode="create_savepoint"` fixture
pattern as `tests_web/test_class_section_api.py`: every adapter-opened
`Session` shares the fixture's single outer, never-committed
`Connection`, so nothing here ever leaves a row in the real database.
The genuine multi-connection concurrency proof (the generation-vs-write
race) lives in `tests_web/test_subject_repository.py` -- this file
proves the HTTP contract, not the underlying lock mechanism.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from school_timetable.api.dependencies import (
    get_generate_schedule_service,
    get_schedule_version_repository,
    get_subject_service,
    get_subjects_projection_service,
    get_teaching_assignments_projection_service,
)
from school_timetable.api.main import app
from school_timetable.application.generate_schedule_service import GenerateScheduleService
from school_timetable.application.subject_projection_service import SubjectProjectionService
from school_timetable.application.subject_service import SubjectService
from school_timetable.application.teaching_assignments_projection_service import (
    TeachingAssignmentsProjectionService,
)
from school_timetable.domain.problem import SchedulingProblem
from school_timetable.fixtures.valid_fixture import build_valid_fixture
from school_timetable.persistence import models as m
from school_timetable.persistence.activity_repository import SqlAlchemyActivityRepository
from school_timetable.persistence.db import get_session
from school_timetable.persistence.problem_repository import SessionFactorySchedulingProblemRepository
from school_timetable.persistence.schedule_repository import SqlAlchemyScheduleVersionRepository
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


def _client(session_factory) -> TestClient:
    def override_get_session():
        yield session_factory()

    def override_schedule_repo():
        return SqlAlchemyScheduleVersionRepository(session_factory)

    def override_generate_service():
        return GenerateScheduleService(
            SessionFactorySchedulingProblemRepository(session_factory),
            SqlAlchemyScheduleVersionRepository(session_factory),
        )

    def override_subjects_projection_service():
        return SubjectProjectionService(
            SessionFactorySchedulingProblemRepository(session_factory),
            SqlAlchemyScheduleVersionRepository(session_factory),
        )

    def override_teaching_assignments_projection_service():
        return TeachingAssignmentsProjectionService(
            SessionFactorySchedulingProblemRepository(session_factory),
            SqlAlchemyScheduleVersionRepository(session_factory),
        )

    def override_subject_service():
        return SubjectService(
            SessionFactorySchedulingProblemRepository(session_factory),
            SqlAlchemyActivityRepository(session_factory),
            SqlAlchemyScheduleVersionRepository(session_factory),
        )

    app.dependency_overrides[get_session] = override_get_session
    app.dependency_overrides[get_schedule_version_repository] = override_schedule_repo
    app.dependency_overrides[get_generate_schedule_service] = override_generate_service
    app.dependency_overrides[get_subjects_projection_service] = override_subjects_projection_service
    app.dependency_overrides[get_teaching_assignments_projection_service] = (
        override_teaching_assignments_projection_service
    )
    app.dependency_overrides[get_subject_service] = override_subject_service
    return TestClient(app)


@pytest.fixture
def client(db):
    _session, session_factory = db
    try:
        yield _client(session_factory)
    finally:
        _clear_overrides()


def _clear_overrides() -> None:
    app.dependency_overrides.pop(get_session, None)
    app.dependency_overrides.pop(get_schedule_version_repository, None)
    app.dependency_overrides.pop(get_generate_schedule_service, None)
    app.dependency_overrides.pop(get_subjects_projection_service, None)
    app.dependency_overrides.pop(get_teaching_assignments_projection_service, None)
    app.dependency_overrides.pop(get_subject_service, None)


def _seed(session: Session) -> SchedulingProblem:
    problem = build_valid_fixture()
    write_scheduling_problem(session, problem)
    session.flush()
    return problem


def _url(problem: SchedulingProblem, *parts: str) -> str:
    return "/".join((f"/schools/{problem.school.id}/years/{problem.academic_year.id}/subjects", *parts))


def _by_id(items: list[dict], item_id: str) -> dict:
    return next(i for i in items if i["id"] == item_id)


# -- GET --------------------------------------------------------------------

def test_get_exact_response_shape(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.get(_url(problem))
    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"configuration_locked", "subjects"}
    assert body["configuration_locked"] is False
    item = body["subjects"][0]
    assert set(item.keys()) == {"id", "name"}


def test_get_ordinary_only_excludes_club(client, db):
    session, _session_factory = db
    problem = _seed(session)

    body = client.get(_url(problem)).json()
    subject_ids = {s["id"] for s in body["subjects"]}
    assert "club_chess" not in subject_ids
    assert "club_robotics" not in subject_ids
    ordinary_activity_ids = [a.id for a in problem.activities if a.kind.value == "ORDINARY"]
    assert [s["id"] for s in body["subjects"]] == ordinary_activity_ids


def test_get_configuration_locked_true_after_generate(client, db):
    session, _session_factory = db
    problem = _seed(session)

    generate_response = client.post(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/generate"
    )
    assert generate_response.status_code == 201

    body = client.get(_url(problem)).json()
    assert body["configuration_locked"] is True


def test_get_unknown_school_year_returns_404(client):
    response = client.get("/schools/no-such-school/years/no-such-year/subjects")
    assert response.status_code == 404
    assert response.json() == {"detail": "Scheduling configuration not found"}


# -- POST -------------------------------------------------------------------

def test_post_create_201_exact_body(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.post(_url(problem), json={"name": "Physics"})
    assert response.status_code == 201
    body = response.json()
    assert set(body.keys()) == {"id", "name"}
    assert body["id"].startswith("activity_")
    assert body["name"] == "Physics"


def test_post_generated_id_never_accepted_from_body(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.post(_url(problem), json={"name": "Physics", "id": "activity_client_supplied", "kind": "CLUB"})
    assert response.status_code == 201
    body = response.json()
    assert body["id"] != "activity_client_supplied"
    assert "kind" not in body


def test_post_persisted_row_kind_is_ordinary(client, db, live_db_engine):
    session, session_factory = db
    problem = _seed(session)

    response = client.post(_url(problem), json={"name": "Physics"})
    assert response.status_code == 201
    subject_natural_id = response.json()["id"]

    check_session = session_factory()
    year_id = check_session.execute(
        select(m.AcademicYear.id).join(m.School, m.School.id == m.AcademicYear.school_id).where(
            m.School.natural_id == problem.school.id, m.AcademicYear.natural_id == problem.academic_year.id,
        )
    ).scalar_one()
    row = check_session.execute(
        select(m.Activity).where(
            m.Activity.academic_year_id == year_id, m.Activity.natural_id == subject_natural_id,
        )
    ).scalar_one()
    assert row.kind == "ORDINARY"
    check_session.close()


def test_post_stores_trimmed_name(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.post(_url(problem), json={"name": "  Physics  "})
    assert response.status_code == 201
    assert response.json()["name"] == "Physics"


def test_post_duplicate_other_ordinary_returns_409(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.post(_url(problem), json={"name": "Mathematics"})
    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "DUPLICATE_SUBJECT"


def test_post_same_name_as_club_succeeds(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.post(_url(problem), json={"name": "Chess Club"})
    assert response.status_code == 201


def test_post_case_variant_succeeds(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.post(_url(problem), json={"name": "mathematics"})
    assert response.status_code == 201


def test_post_blank_name_returns_422(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.post(_url(problem), json={"name": "   "})
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "INVALID_SUBJECT"
    assert any(e["code"] == "BLANK_SUBJECT_NAME" for e in body["errors"])


def test_post_locked_configuration_returns_409_exact_body(client, db):
    session, _session_factory = db
    problem = _seed(session)

    generate_response = client.post(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/generate"
    )
    assert generate_response.status_code == 201

    response = client.post(_url(problem), json={"name": "Physics"})
    assert response.status_code == 409
    assert response.json() == {
        "code": "SCHEDULING_CONFIGURATION_LOCKED",
        "detail": "Scheduling configuration is locked because a schedule already exists",
    }


# -- PUT --------------------------------------------------------------------

def test_put_update_200_preserves_id(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.put(_url(problem, "math"), json={"name": "Advanced Mathematics"})
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "math"
    assert body["name"] == "Advanced Mathematics"


def test_put_not_found_returns_404(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.put(_url(problem, "does-not-exist"), json={"name": "X"})
    assert response.status_code == 404
    assert response.json() == {"detail": "Subject not found"}


def test_put_club_id_returns_404_and_club_unchanged(client, db, live_db_engine):
    session, session_factory = db
    problem = _seed(session)

    response = client.put(_url(problem, "club_chess"), json={"name": "Renamed"})
    assert response.status_code == 404
    assert response.json() == {"detail": "Subject not found"}

    check_session = session_factory()
    year_id = check_session.execute(
        select(m.AcademicYear.id).join(m.School, m.School.id == m.AcademicYear.school_id).where(
            m.School.natural_id == problem.school.id, m.AcademicYear.natural_id == problem.academic_year.id,
        )
    ).scalar_one()
    row = check_session.execute(
        select(m.Activity).where(
            m.Activity.academic_year_id == year_id, m.Activity.natural_id == "club_chess",
        )
    ).scalar_one()
    assert row.name == "Chess Club"
    assert row.kind == "CLUB"
    check_session.close()


def test_put_duplicate_other_ordinary_returns_409(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.put(_url(problem, "math"), json={"name": "Science"})
    assert response.status_code == 409
    assert response.json()["code"] == "DUPLICATE_SUBJECT"


def test_put_locked_configuration_returns_409(client, db):
    session, _session_factory = db
    problem = _seed(session)

    generate_response = client.post(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/generate"
    )
    assert generate_response.status_code == 201

    response = client.put(_url(problem, "math"), json={"name": "Renamed"})
    assert response.status_code == 409
    assert response.json()["code"] == "SCHEDULING_CONFIGURATION_LOCKED"


def test_put_rename_visible_immediately_in_subjects_config_and_assignments(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.put(_url(problem, "math"), json={"name": "Advanced Mathematics"})
    assert response.status_code == 200

    subjects_body = client.get(_url(problem)).json()
    assert _by_id(subjects_body["subjects"], "math")["name"] == "Advanced Mathematics"

    config_body = client.get(f"/schools/{problem.school.id}/years/{problem.academic_year.id}/config").json()
    config_activity = _by_id(config_body["activities"], "math")
    assert config_activity["name"] == "Advanced Mathematics"
    assert config_activity["kind"] == "ORDINARY"

    assignments_body = client.get(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/teaching-assignments"
    ).json()
    option = _by_id(assignments_body["activities"], "math")
    assert option["name"] == "Advanced Mathematics"


# -- DELETE -------------------------------------------------------------

def test_delete_unused_ordinary_200_exact_body(client, db):
    session, _session_factory = db
    problem = _seed(session)

    create_response = client.post(_url(problem), json={"name": "Physics"})
    new_id = create_response.json()["id"]

    response = client.delete(_url(problem, new_id))
    assert response.status_code == 200
    assert response.json() == {"deleted_id": new_id}

    body = client.get(_url(problem)).json()
    assert new_id not in {s["id"] for s in body["subjects"]}


def test_delete_not_found_returns_404(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.delete(_url(problem, "does-not-exist"))
    assert response.status_code == 404
    assert response.json() == {"detail": "Subject not found"}


def test_delete_club_id_returns_404_and_club_remains(client, db, live_db_engine):
    session, session_factory = db
    problem = _seed(session)

    response = client.delete(_url(problem, "club_chess"))
    assert response.status_code == 404
    assert response.json() == {"detail": "Subject not found"}

    check_session = session_factory()
    year_id = check_session.execute(
        select(m.AcademicYear.id).join(m.School, m.School.id == m.AcademicYear.school_id).where(
            m.School.natural_id == problem.school.id, m.AcademicYear.natural_id == problem.academic_year.id,
        )
    ).scalar_one()
    row = check_session.execute(
        select(m.Activity).where(
            m.Activity.academic_year_id == year_id, m.Activity.natural_id == "club_chess",
        )
    ).scalar_one_or_none()
    assert row is not None
    check_session.close()


def test_delete_teaching_requirement_reference_returns_409(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.delete(_url(problem, "math"))
    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "SUBJECT_IN_USE"
    assert "TEACHING_REQUIREMENT" in body["referenced_by"]

    get_body = client.get(_url(problem)).json()
    assert "math" in {s["id"] for s in get_body["subjects"]}


def test_delete_locked_configuration_returns_409(client, db):
    session, _session_factory = db
    problem = _seed(session)

    generate_response = client.post(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/generate"
    )
    assert generate_response.status_code == 201

    # The fast lock precheck runs before any existence check, so this
    # rejects with the lock error regardless of "math" being referenced.
    response = client.delete(_url(problem, "math"))
    assert response.status_code == 409
    assert response.json()["code"] == "SCHEDULING_CONFIGURATION_LOCKED"


# -- INTEROPERABILITY: cross-flow visibility ---------------------------------

def test_create_visible_across_config_and_teaching_assignments(client, db):
    session, _session_factory = db
    problem = _seed(session)

    create_response = client.post(_url(problem), json={"name": "Physics"})
    assert create_response.status_code == 201
    new_id = create_response.json()["id"]

    subjects_body = client.get(_url(problem)).json()
    created = _by_id(subjects_body["subjects"], new_id)
    assert created["name"] == "Physics"

    config_body = client.get(f"/schools/{problem.school.id}/years/{problem.academic_year.id}/config").json()
    config_activity = _by_id(config_body["activities"], new_id)
    assert config_activity["name"] == "Physics"
    assert config_activity["kind"] == "ORDINARY"

    assignments_body = client.get(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/teaching-assignments"
    ).json()
    option = _by_id(assignments_body["activities"], new_id)
    assert option["name"] == "Physics"
