"""Real-PostgreSQL HTTP integration tests for Resources Slice A's
`GET/POST .../resources` and `PUT/DELETE .../resources/{resource_id}`.

Uses the same `join_transaction_mode="create_savepoint"` fixture
pattern as `tests_web/test_special_activity_api.py`: every
adapter-opened `Session` shares the fixture's single outer,
never-committed `Connection`, so nothing here ever leaves a row in the
real database. The genuine multi-connection concurrency proof (the
generation-vs-write race) lives in
`tests_web/test_resource_repository.py` -- this file proves the HTTP
contract, not the underlying lock mechanism.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from school_timetable.api.dependencies import (
    get_generate_schedule_service,
    get_resource_service,
    get_resources_projection_service,
    get_schedule_version_repository,
)
from school_timetable.api.main import app
from school_timetable.application.generate_schedule_service import GenerateScheduleService
from school_timetable.application.resource_projection_service import ResourceProjectionService
from school_timetable.application.resource_service import ResourceService
from school_timetable.domain.problem import SchedulingProblem
from school_timetable.fixtures.valid_fixture import build_valid_fixture
from school_timetable.persistence import models as m
from school_timetable.persistence.db import get_session
from school_timetable.persistence.problem_repository import SessionFactorySchedulingProblemRepository
from school_timetable.persistence.resource_repository import SqlAlchemyResourceRepository
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

    def override_resources_projection_service():
        return ResourceProjectionService(
            SessionFactorySchedulingProblemRepository(session_factory),
            SqlAlchemyScheduleVersionRepository(session_factory),
        )

    def override_resource_service():
        return ResourceService(
            SessionFactorySchedulingProblemRepository(session_factory),
            SqlAlchemyResourceRepository(session_factory),
            SqlAlchemyScheduleVersionRepository(session_factory),
        )

    app.dependency_overrides[get_session] = override_get_session
    app.dependency_overrides[get_schedule_version_repository] = override_schedule_repo
    app.dependency_overrides[get_generate_schedule_service] = override_generate_service
    app.dependency_overrides[get_resources_projection_service] = override_resources_projection_service
    app.dependency_overrides[get_resource_service] = override_resource_service
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
    app.dependency_overrides.pop(get_resources_projection_service, None)
    app.dependency_overrides.pop(get_resource_service, None)


def _seed(session: Session) -> SchedulingProblem:
    problem = build_valid_fixture()
    write_scheduling_problem(session, problem)
    session.flush()
    return problem


def _url(problem: SchedulingProblem, *parts: str) -> str:
    return "/".join((f"/schools/{problem.school.id}/years/{problem.academic_year.id}/resources", *parts))


def _by_id(items: list[dict], item_id: str) -> dict:
    return next(i for i in items if i["id"] == item_id)


# -- GET --------------------------------------------------------------------

def test_get_exact_response_shape(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.get(_url(problem))
    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"configuration_locked", "resources"}
    assert body["configuration_locked"] is False
    item = body["resources"][0]
    assert set(item.keys()) == {"id", "name", "capacity"}


def test_get_includes_fixture_gym(client, db):
    session, _session_factory = db
    problem = _seed(session)

    body = client.get(_url(problem)).json()
    gym = _by_id(body["resources"], "gym")
    assert gym["name"] == "Indoor Gym"
    assert gym["capacity"] == 1


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
    response = client.get("/schools/no-such-school/years/no-such-year/resources")
    assert response.status_code == 404
    assert response.json() == {"detail": "Scheduling configuration not found"}


# -- POST -------------------------------------------------------------------

def test_post_create_201_exact_body(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.post(_url(problem), json={"name": "Music Room", "capacity": 1})
    assert response.status_code == 201
    body = response.json()
    assert set(body.keys()) == {"id", "name", "capacity"}
    assert body["id"].startswith("resource_")
    assert body["name"] == "Music Room"
    assert body["capacity"] == 1


def test_post_generated_id_never_accepted_from_body(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.post(
        _url(problem), json={"name": "Music Room", "capacity": 1, "id": "resource_client_supplied"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["id"] != "resource_client_supplied"


def test_post_stores_trimmed_name(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.post(_url(problem), json={"name": "  Music Room  ", "capacity": 1})
    assert response.status_code == 201
    assert response.json()["name"] == "Music Room"


def test_post_capacity_greater_than_one_persists(client, db, live_db_engine):
    session, session_factory = db
    problem = _seed(session)

    response = client.post(_url(problem), json={"name": "Laptop Carts", "capacity": 5})
    assert response.status_code == 201
    resource_natural_id = response.json()["id"]

    check_session = session_factory()
    year_id = check_session.execute(
        select(m.AcademicYear.id).join(m.School, m.School.id == m.AcademicYear.school_id).where(
            m.School.natural_id == problem.school.id, m.AcademicYear.natural_id == problem.academic_year.id,
        )
    ).scalar_one()
    row = check_session.execute(
        select(m.Resource).where(
            m.Resource.academic_year_id == year_id, m.Resource.natural_id == resource_natural_id,
        )
    ).scalar_one()
    assert row.capacity == 5
    check_session.close()


def test_post_duplicate_other_resource_returns_409(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.post(_url(problem), json={"name": "Indoor Gym", "capacity": 1})
    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "DUPLICATE_RESOURCE"


def test_post_same_name_as_ordinary_subject_succeeds(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.post(_url(problem), json={"name": "Mathematics", "capacity": 1})
    assert response.status_code == 201


def test_post_case_variant_succeeds(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.post(_url(problem), json={"name": "indoor gym", "capacity": 1})
    assert response.status_code == 201


def test_post_blank_name_returns_422(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.post(_url(problem), json={"name": "   ", "capacity": 1})
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "INVALID_RESOURCE"
    assert any(e["code"] == "BLANK_RESOURCE_NAME" for e in body["errors"])


def test_post_capacity_zero_returns_422(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.post(_url(problem), json={"name": "Music Room", "capacity": 0})
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "INVALID_RESOURCE"
    assert any(e["code"] == "INVALID_RESOURCE_CAPACITY" for e in body["errors"])


def test_post_capacity_negative_returns_422(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.post(_url(problem), json={"name": "Music Room", "capacity": -2})
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "INVALID_RESOURCE"
    assert any(e["code"] == "INVALID_RESOURCE_CAPACITY" for e in body["errors"])


def test_post_locked_configuration_returns_409_exact_body(client, db):
    session, _session_factory = db
    problem = _seed(session)

    generate_response = client.post(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/generate"
    )
    assert generate_response.status_code == 201

    response = client.post(_url(problem), json={"name": "Music Room", "capacity": 1})
    assert response.status_code == 409
    assert response.json() == {
        "code": "SCHEDULING_CONFIGURATION_LOCKED",
        "detail": "Scheduling configuration is locked because a schedule already exists",
    }


# -- PUT --------------------------------------------------------------------

def test_put_update_200_preserves_id(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.put(_url(problem, "gym"), json={"name": "Sports Hall", "capacity": 2})
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "gym"
    assert body["name"] == "Sports Hall"
    assert body["capacity"] == 2


def test_put_not_found_returns_404(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.put(_url(problem, "does-not-exist"), json={"name": "X", "capacity": 1})
    assert response.status_code == 404
    assert response.json() == {"detail": "Resource not found"}


def test_put_capacity_zero_returns_422_and_row_unchanged(client, db, live_db_engine):
    session, session_factory = db
    problem = _seed(session)

    response = client.put(_url(problem, "gym"), json={"name": "Indoor Gym", "capacity": 0})
    assert response.status_code == 422

    check_session = session_factory()
    year_id = check_session.execute(
        select(m.AcademicYear.id).join(m.School, m.School.id == m.AcademicYear.school_id).where(
            m.School.natural_id == problem.school.id, m.AcademicYear.natural_id == problem.academic_year.id,
        )
    ).scalar_one()
    row = check_session.execute(
        select(m.Resource).where(m.Resource.academic_year_id == year_id, m.Resource.natural_id == "gym")
    ).scalar_one()
    assert row.capacity == 1
    check_session.close()


def test_put_duplicate_other_resource_returns_409(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.post(_url(problem), json={"name": "Music Room", "capacity": 1})
    assert response.status_code == 201

    response = client.put(_url(problem, "gym"), json={"name": "Music Room", "capacity": 1})
    assert response.status_code == 409
    assert response.json()["code"] == "DUPLICATE_RESOURCE"


def test_put_locked_configuration_returns_409(client, db):
    session, _session_factory = db
    problem = _seed(session)

    generate_response = client.post(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/generate"
    )
    assert generate_response.status_code == 201

    response = client.put(_url(problem, "gym"), json={"name": "Renamed", "capacity": 1})
    assert response.status_code == 409
    assert response.json()["code"] == "SCHEDULING_CONFIGURATION_LOCKED"


# -- DELETE -------------------------------------------------------------

def test_delete_unused_resource_200_exact_body(client, db):
    session, _session_factory = db
    problem = _seed(session)

    create_response = client.post(_url(problem), json={"name": "Music Room", "capacity": 1})
    new_id = create_response.json()["id"]

    response = client.delete(_url(problem, new_id))
    assert response.status_code == 200
    assert response.json() == {"deleted_id": new_id}

    body = client.get(_url(problem)).json()
    assert new_id not in {r["id"] for r in body["resources"]}


def test_delete_not_found_returns_404(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.delete(_url(problem, "does-not-exist"))
    assert response.status_code == 404
    assert response.json() == {"detail": "Resource not found"}


def test_delete_teaching_requirement_reference_returns_409(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.delete(_url(problem, "gym"))
    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "RESOURCE_IN_USE"
    assert body["referenced_by"] == ["TEACHING_REQUIREMENT"]

    get_body = client.get(_url(problem)).json()
    assert "gym" in {r["id"] for r in get_body["resources"]}


def test_delete_locked_configuration_returns_409(client, db):
    session, _session_factory = db
    problem = _seed(session)

    generate_response = client.post(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/generate"
    )
    assert generate_response.status_code == 201

    # The fast lock precheck runs before any existence check, so this
    # rejects with the lock error regardless of "gym" being referenced.
    response = client.delete(_url(problem, "gym"))
    assert response.status_code == 409
    assert response.json()["code"] == "SCHEDULING_CONFIGURATION_LOCKED"


# -- INTEROPERABILITY: cross-flow visibility ---------------------------------

def test_create_visible_across_config(client, db):
    session, _session_factory = db
    problem = _seed(session)

    create_response = client.post(_url(problem), json={"name": "Music Room", "capacity": 2})
    assert create_response.status_code == 201
    new_id = create_response.json()["id"]

    resources_body = client.get(_url(problem)).json()
    created = _by_id(resources_body["resources"], new_id)
    assert created["name"] == "Music Room"
    assert created["capacity"] == 2

    config_body = client.get(f"/schools/{problem.school.id}/years/{problem.academic_year.id}/config").json()
    config_resource = _by_id(config_body["resources"], new_id)
    assert config_resource["name"] == "Music Room"
    assert config_resource["capacity"] == 2


def test_update_visible_across_config(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.put(_url(problem, "gym"), json={"name": "Sports Hall", "capacity": 3})
    assert response.status_code == 200

    resources_body = client.get(_url(problem)).json()
    assert _by_id(resources_body["resources"], "gym")["name"] == "Sports Hall"

    config_body = client.get(f"/schools/{problem.school.id}/years/{problem.academic_year.id}/config").json()
    config_resource = _by_id(config_body["resources"], "gym")
    assert config_resource["name"] == "Sports Hall"
    assert config_resource["capacity"] == 3


def test_delete_removed_from_config(client, db):
    session, _session_factory = db
    problem = _seed(session)

    create_response = client.post(_url(problem), json={"name": "Music Room", "capacity": 1})
    new_id = create_response.json()["id"]

    delete_response = client.delete(_url(problem, new_id))
    assert delete_response.status_code == 200

    config_body = client.get(f"/schools/{problem.school.id}/years/{problem.academic_year.id}/config").json()
    assert new_id not in {r["id"] for r in config_body["resources"]}
