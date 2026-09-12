"""Real-PostgreSQL HTTP integration tests for Real-School Setup MVP
Slice B's `GET/POST .../teachers` and `PUT/DELETE .../teachers/{teacher_id}`.

Uses the same `join_transaction_mode="create_savepoint"` fixture
pattern as `tests_web/test_teaching_assignment_api.py`: every
adapter-opened `Session` shares the fixture's single outer,
never-committed `Connection`, so nothing here ever leaves a row in the
real database. The genuine multi-connection concurrency proof (the
generation-vs-write race) lives in `tests_web/test_teacher_repository.py`
-- this file proves the HTTP contract, not the underlying lock mechanism.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from school_timetable.api.dependencies import (
    get_generate_schedule_service,
    get_schedule_version_repository,
    get_teacher_service,
    get_teaching_assignments_projection_service,
    get_teachers_projection_service,
)
from school_timetable.api.main import app
from school_timetable.application.generate_schedule_service import GenerateScheduleService
from school_timetable.application.teacher_projection_service import TeacherProjectionService
from school_timetable.application.teacher_service import TeacherService
from school_timetable.application.teaching_assignments_projection_service import (
    TeachingAssignmentsProjectionService,
)
from school_timetable.domain.problem import SchedulingProblem
from school_timetable.fixtures.valid_fixture import build_valid_fixture
from school_timetable.persistence.configuration_revision_repository import (
    SqlAlchemyConfigurationRevisionRepository,
)
from school_timetable.persistence.db import get_session
from school_timetable.persistence.problem_repository import SessionFactorySchedulingProblemRepository
from school_timetable.persistence.schedule_repository import SqlAlchemyScheduleVersionRepository
from school_timetable.persistence.teacher_repository import SqlAlchemyTeacherRepository
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

    def override_teachers_projection_service():
        return TeacherProjectionService(
            SessionFactorySchedulingProblemRepository(session_factory),
            SqlAlchemyConfigurationRevisionRepository(session_factory),
        )

    def override_teaching_assignments_projection_service():
        return TeachingAssignmentsProjectionService(
            SessionFactorySchedulingProblemRepository(session_factory),
            SqlAlchemyConfigurationRevisionRepository(session_factory),
        )

    def override_teacher_service():
        return TeacherService(
            SessionFactorySchedulingProblemRepository(session_factory),
            SqlAlchemyTeacherRepository(session_factory),
        )

    app.dependency_overrides[get_session] = override_get_session
    app.dependency_overrides[get_schedule_version_repository] = override_schedule_repo
    app.dependency_overrides[get_generate_schedule_service] = override_generate_service
    app.dependency_overrides[get_teachers_projection_service] = override_teachers_projection_service
    app.dependency_overrides[get_teaching_assignments_projection_service] = (
        override_teaching_assignments_projection_service
    )
    app.dependency_overrides[get_teacher_service] = override_teacher_service
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
    app.dependency_overrides.pop(get_teachers_projection_service, None)
    app.dependency_overrides.pop(get_teaching_assignments_projection_service, None)
    app.dependency_overrides.pop(get_teacher_service, None)


def _seed(session: Session) -> SchedulingProblem:
    problem = build_valid_fixture()
    write_scheduling_problem(session, problem)
    session.flush()
    return problem


def _url(problem: SchedulingProblem, *parts: str) -> str:
    return "/".join((f"/schools/{problem.school.id}/years/{problem.academic_year.id}/teachers", *parts))


def _by_id(items: list[dict], item_id: str) -> dict:
    return next(i for i in items if i["id"] == item_id)


# -- GET --------------------------------------------------------------------

def test_get_exact_response_shape(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.get(_url(problem))
    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"configuration_locked", "teachers"}
    assert body["configuration_locked"] is False
    assert len(body["teachers"]) == len(problem.teachers)
    item = body["teachers"][0]
    assert set(item.keys()) == {"id", "first_name", "last_name", "name"}


def test_get_ordinal_ordering_deterministic(client, db):
    session, _session_factory = db
    problem = _seed(session)

    body = client.get(_url(problem)).json()
    assert [t["id"] for t in body["teachers"]] == [t.id for t in problem.teachers]


def test_get_configuration_locked_true_after_generate(client, db):
    session, _session_factory = db
    problem = _seed(session)

    generate_response = client.post(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/generate"
    )
    assert generate_response.status_code == 201

    body = client.get(_url(problem)).json()
    assert body["configuration_locked"] is True
    assert len(body["teachers"]) == len(problem.teachers)


def test_get_unknown_school_year_returns_404(client):
    response = client.get("/schools/no-such-school/years/no-such-year/teachers")
    assert response.status_code == 404
    assert response.json() == {"detail": "Scheduling configuration not found"}


# -- POST -------------------------------------------------------------------

def test_post_create_201_exact_body(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.post(_url(problem), json={"first_name": "George", "last_name": "Kakochashvili"})
    assert response.status_code == 201
    body = response.json()
    assert set(body.keys()) == {"id", "first_name", "last_name", "name"}
    assert body["id"].startswith("teacher_")
    assert body["first_name"] == "George"
    assert body["last_name"] == "Kakochashvili"
    assert body["name"] == "George Kakochashvili"


def test_post_generated_id_never_accepted_from_body(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.post(
        _url(problem),
        json={"first_name": "George", "last_name": "Kakochashvili", "id": "teacher_client_supplied"},
    )
    assert response.status_code == 201
    assert response.json()["id"] != "teacher_client_supplied"


def test_post_stores_trimmed_names(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.post(_url(problem), json={"first_name": "  George  ", "last_name": "  Kakochashvili  "})
    assert response.status_code == 201
    body = response.json()
    assert body["first_name"] == "George"
    assert body["last_name"] == "Kakochashvili"


def test_post_same_name_teachers_coexist(client, db):
    session, _session_factory = db
    problem = _seed(session)

    first = client.post(_url(problem), json={"first_name": "Nino", "last_name": "Beridze"})
    second = client.post(_url(problem), json={"first_name": "Nino", "last_name": "Beridze"})
    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] != second.json()["id"]


def test_post_blank_first_name_returns_422(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.post(_url(problem), json={"first_name": "   ", "last_name": "Beridze"})
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "INVALID_TEACHER"
    assert any(e["code"] == "BLANK_FIRST_NAME" for e in body["errors"])


def test_post_blank_last_name_returns_422(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.post(_url(problem), json={"first_name": "Nino", "last_name": "   "})
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "INVALID_TEACHER"
    assert any(e["code"] == "BLANK_LAST_NAME" for e in body["errors"])


def test_post_locked_configuration_returns_409_exact_body(client, db):
    session, _session_factory = db
    problem = _seed(session)

    generate_response = client.post(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/generate"
    )
    assert generate_response.status_code == 201

    response = client.post(_url(problem), json={"first_name": "George", "last_name": "Kakochashvili"})
    assert response.status_code == 409
    assert response.json() == {
        "code": "SCHEDULING_CONFIGURATION_LOCKED",
        "detail": "Scheduling configuration is locked because a schedule already exists",
    }


# -- PUT --------------------------------------------------------------------

def test_put_update_200_preserves_natural_id(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.put(_url(problem, "t_math"), json={"first_name": "New", "last_name": "Name"})
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "t_math"
    assert body["first_name"] == "New"
    assert body["last_name"] == "Name"
    assert body["name"] == "New Name"


def test_put_ordinal_unchanged_after_update(client, db):
    session, session_factory = db
    problem = _seed(session)

    client.put(_url(problem, "t_math"), json={"first_name": "New", "last_name": "Name"})

    body = client.get(_url(problem)).json()
    assert [t["id"] for t in body["teachers"]] == [t.id for t in problem.teachers]


def test_put_not_found_returns_404(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.put(_url(problem, "does-not-exist"), json={"first_name": "A", "last_name": "B"})
    assert response.status_code == 404
    assert response.json() == {"detail": "Teacher not found"}


def test_put_blank_field_returns_422(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.put(_url(problem, "t_math"), json={"first_name": "", "last_name": "Name"})
    assert response.status_code == 422
    assert response.json()["code"] == "INVALID_TEACHER"


def test_put_locked_configuration_returns_409(client, db):
    session, _session_factory = db
    problem = _seed(session)

    generate_response = client.post(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/generate"
    )
    assert generate_response.status_code == 201

    response = client.put(_url(problem, "t_math"), json={"first_name": "New", "last_name": "Name"})
    assert response.status_code == 409
    assert response.json()["code"] == "SCHEDULING_CONFIGURATION_LOCKED"


def test_put_update_visible_immediately_in_config_and_projections(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.put(_url(problem, "t_math"), json={"first_name": "New", "last_name": "Name"})
    assert response.status_code == 200

    config_body = client.get(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/config"
    ).json()
    assert _by_id(config_body["teachers"], "t_math")["name"] == "New Name"

    assignments_body = client.get(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/teaching-assignments"
    ).json()
    assert _by_id(assignments_body["teachers"], "t_math")["name"] == "New Name"


# -- DELETE -------------------------------------------------------------

def test_delete_unused_teacher_200_exact_body(client, db):
    session, session_factory = db
    problem = _seed(session)

    create_response = client.post(_url(problem), json={"first_name": "Unused", "last_name": "Teacher"})
    new_id = create_response.json()["id"]

    response = client.delete(_url(problem, new_id))
    assert response.status_code == 200
    assert response.json() == {"deleted_id": new_id}

    body = client.get(_url(problem)).json()
    assert new_id not in {t["id"] for t in body["teachers"]}


def test_delete_not_found_returns_404(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.delete(_url(problem, "does-not-exist"))
    assert response.status_code == 404
    assert response.json() == {"detail": "Teacher not found"}


def test_delete_teaching_requirement_reference_returns_409(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.delete(_url(problem, "t_math"))
    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "TEACHER_IN_USE"
    assert body["referenced_by"] == ["TEACHING_REQUIREMENT"]

    # The referenced teacher and its requirements must not be deleted.
    get_body = client.get(_url(problem)).json()
    assert "t_math" in {t["id"] for t in get_body["teachers"]}


def test_delete_teacher_availability_reference_returns_409(client, db):
    session, _session_factory = db
    problem = _seed(session)

    # t_science has both a TeachingRequirement AND a TeacherAvailability
    # reference in the fixture -- TEACHING_REQUIREMENT sorts first.
    response = client.delete(_url(problem, "t_science"))
    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "TEACHER_IN_USE"
    assert "TEACHING_REQUIREMENT" in body["referenced_by"]


def test_delete_locked_configuration_returns_409(client, db):
    session, session_factory = db
    problem = _seed(session)

    create_response = client.post(_url(problem), json={"first_name": "Unused", "last_name": "Teacher"})
    new_id = create_response.json()["id"]

    generate_response = client.post(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/generate"
    )
    assert generate_response.status_code == 201

    response = client.delete(_url(problem, new_id))
    assert response.status_code == 409
    assert response.json()["code"] == "SCHEDULING_CONFIGURATION_LOCKED"


# -- INTEROPERABILITY: cross-flow visibility ---------------------------------

def test_create_visible_across_config_and_teaching_assignments(client, db):
    session, _session_factory = db
    problem = _seed(session)

    create_response = client.post(_url(problem), json={"first_name": "George", "last_name": "Kakochashvili"})
    assert create_response.status_code == 201
    new_id = create_response.json()["id"]

    teachers_body = client.get(_url(problem)).json()
    created = _by_id(teachers_body["teachers"], new_id)
    assert created["first_name"] == "George"
    assert created["last_name"] == "Kakochashvili"
    assert created["name"] == "George Kakochashvili"

    config_body = client.get(f"/schools/{problem.school.id}/years/{problem.academic_year.id}/config").json()
    assert _by_id(config_body["teachers"], new_id)["name"] == "George Kakochashvili"

    assignments_body = client.get(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/teaching-assignments"
    ).json()
    option = _by_id(assignments_body["teachers"], new_id)
    assert option["name"] == "George Kakochashvili"
    workload = next(w for w in assignments_body["teacher_workloads"] if w["teacher_id"] == new_id)
    assert workload["total_weekly_periods"] == 0
