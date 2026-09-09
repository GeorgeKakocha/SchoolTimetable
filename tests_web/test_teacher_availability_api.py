"""Real-PostgreSQL HTTP integration tests for Owner Decision #38's
`GET /schools/{school_id}/years/{year_id}/teacher-availability` and
`PUT .../teacher-availability/{teacher_id}`.

Uses the same `join_transaction_mode="create_savepoint"` fixture
pattern as `tests_web/test_subject_api.py`: every adapter-opened
`Session` shares the fixture's single outer, never-committed
`Connection`, so nothing here ever leaves a row in the real database.
The genuine multi-connection concurrency proof (the generation-vs-write
race) lives in `tests_web/test_teacher_availability_repository.py` --
this file proves the HTTP contract, not the underlying lock mechanism.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from school_timetable.api.dependencies import (
    get_generate_schedule_service,
    get_schedule_version_repository,
    get_teacher_availability_projection_service,
    get_teacher_availability_service,
)
from school_timetable.api.main import app
from school_timetable.application.generate_schedule_service import GenerateScheduleService
from school_timetable.application.teacher_availability_projection_service import (
    TeacherAvailabilityProjectionService,
)
from school_timetable.application.teacher_availability_service import TeacherAvailabilityService
from school_timetable.domain.problem import SchedulingProblem
from school_timetable.fixtures.valid_fixture import build_valid_fixture
from school_timetable.persistence.db import get_session
from school_timetable.persistence.problem_repository import SessionFactorySchedulingProblemRepository
from school_timetable.persistence.schedule_repository import SqlAlchemyScheduleVersionRepository
from school_timetable.persistence.teacher_availability_repository import SqlAlchemyTeacherAvailabilityRepository
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

    def override_projection_service():
        return TeacherAvailabilityProjectionService(
            SessionFactorySchedulingProblemRepository(session_factory),
            SqlAlchemyScheduleVersionRepository(session_factory),
        )

    def override_write_service():
        return TeacherAvailabilityService(
            SessionFactorySchedulingProblemRepository(session_factory),
            SqlAlchemyTeacherAvailabilityRepository(session_factory),
            SqlAlchemyScheduleVersionRepository(session_factory),
        )

    app.dependency_overrides[get_session] = override_get_session
    app.dependency_overrides[get_schedule_version_repository] = override_schedule_repo
    app.dependency_overrides[get_generate_schedule_service] = override_generate_service
    app.dependency_overrides[get_teacher_availability_projection_service] = override_projection_service
    app.dependency_overrides[get_teacher_availability_service] = override_write_service
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
    app.dependency_overrides.pop(get_teacher_availability_projection_service, None)
    app.dependency_overrides.pop(get_teacher_availability_service, None)


def _seed(session: Session) -> SchedulingProblem:
    problem = build_valid_fixture()
    write_scheduling_problem(session, problem)
    session.flush()
    return problem


def _get_url(problem: SchedulingProblem) -> str:
    return f"/schools/{problem.school.id}/years/{problem.academic_year.id}/teacher-availability"


def _put_url(problem: SchedulingProblem, teacher_id: str) -> str:
    return f"{_get_url(problem)}/{teacher_id}"


def _config_url(problem: SchedulingProblem) -> str:
    return f"/schools/{problem.school.id}/years/{problem.academic_year.id}/config"


# -- GET --------------------------------------------------------------------

def test_get_exact_shape(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.get(_get_url(problem))
    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"configuration_locked", "teachers", "days", "periods", "exceptions"}
    assert set(body["teachers"][0].keys()) == {"id", "name"}
    assert set(body["days"][0].keys()) == {"id", "name", "index"}
    assert set(body["periods"][0].keys()) == {"id", "name", "index", "block_id", "is_instructional"}
    assert set(body["exceptions"][0].keys()) == {"teacher_id", "day_id", "period_id", "status"}


def test_get_teachers_days_periods_preserve_authoritative_order(client, db):
    session, _session_factory = db
    problem = _seed(session)

    body = client.get(_get_url(problem)).json()
    assert [t["id"] for t in body["teachers"]] == [t.id for t in problem.teachers]
    assert [d["id"] for d in body["days"]] == [d.id for d in problem.days]
    assert [p["id"] for p in body["periods"]] == [p.id for p in problem.periods]


def test_get_sparse_exceptions_include_prefer_not_and_unavailable(client, db):
    session, _session_factory = db
    problem = _seed(session)

    body = client.get(_get_url(problem)).json()
    exceptions = body["exceptions"]
    assert any(e["teacher_id"] == "t_science" and e["status"] == "UNAVAILABLE" for e in exceptions)
    assert any(e["teacher_id"] == "t_history" and e["status"] == "PREFER_NOT" for e in exceptions)
    assert all(e["status"] != "AVAILABLE" for e in exceptions)


def test_get_configuration_locked_false_initially(client, db):
    session, _session_factory = db
    problem = _seed(session)

    body = client.get(_get_url(problem)).json()
    assert body["configuration_locked"] is False


def test_get_configuration_locked_true_after_generate(client, db):
    session, _session_factory = db
    problem = _seed(session)

    generate_response = client.post(f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/generate")
    assert generate_response.status_code == 201

    body = client.get(_get_url(problem)).json()
    assert body["configuration_locked"] is True


def test_get_unknown_school_year_returns_404(client):
    response = client.get("/schools/no-such-school/years/no-such-year/teacher-availability")
    assert response.status_code == 404
    assert response.json() == {"detail": "Scheduling configuration not found"}


# -- PUT: SUCCESS -------------------------------------------------------------

def test_put_prefer_not_succeeds_with_exact_response(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.put(
        _put_url(problem, "t_math"), json={"exceptions": [{"day_id": "mon", "period_id": "p5", "status": "PREFER_NOT"}]},
    )
    assert response.status_code == 200
    assert response.json() == {
        "teacher_id": "t_math",
        "exceptions": [{"day_id": "mon", "period_id": "p5", "status": "PREFER_NOT"}],
    }


def test_put_unavailable_succeeds(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.put(
        _put_url(problem, "t_math"), json={"exceptions": [{"day_id": "mon", "period_id": "p5", "status": "UNAVAILABLE"}]},
    )
    assert response.status_code == 200
    assert response.json()["exceptions"] == [{"day_id": "mon", "period_id": "p5", "status": "UNAVAILABLE"}]


def test_put_mixed_succeeds(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.put(
        _put_url(problem, "t_math"),
        json={"exceptions": [
            {"day_id": "mon", "period_id": "p5", "status": "UNAVAILABLE"},
            {"day_id": "tue", "period_id": "p6", "status": "PREFER_NOT"},
        ]},
    )
    assert response.status_code == 200
    assert {(e["day_id"], e["period_id"], e["status"]) for e in response.json()["exceptions"]} == {
        ("mon", "p5", "UNAVAILABLE"), ("tue", "p6", "PREFER_NOT"),
    }


def test_put_get_reflects_saved_exceptions(client, db):
    session, _session_factory = db
    problem = _seed(session)

    client.put(_put_url(problem, "t_math"), json={"exceptions": [{"day_id": "mon", "period_id": "p5", "status": "UNAVAILABLE"}]})

    body = client.get(_get_url(problem)).json()
    assert any(
        e["teacher_id"] == "t_math" and e["day_id"] == "mon" and e["period_id"] == "p5" and e["status"] == "UNAVAILABLE"
        for e in body["exceptions"]
    )


def test_put_config_also_reflects_persisted_rows(client, db):
    session, _session_factory = db
    problem = _seed(session)

    client.put(_put_url(problem, "t_math"), json={"exceptions": [{"day_id": "mon", "period_id": "p5", "status": "UNAVAILABLE"}]})

    config_body = client.get(_config_url(problem)).json()
    assert any(
        a["teacher_id"] == "t_math" and a["day_id"] == "mon" and a["period_id"] == "p5" and a["status"] == "UNAVAILABLE"
        for a in config_body["teacher_availabilities"]
    )


def test_put_empty_list_clears_exceptions(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.put(_put_url(problem, "t_science"), json={"exceptions": []})
    assert response.status_code == 200
    assert response.json()["exceptions"] == []

    body = client.get(_get_url(problem)).json()
    assert not any(e["teacher_id"] == "t_science" for e in body["exceptions"])


# -- PUT: VALIDATION -----------------------------------------------------

def test_put_available_rejected(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.put(
        _put_url(problem, "t_math"), json={"exceptions": [{"day_id": "mon", "period_id": "p5", "status": "AVAILABLE"}]},
    )
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "INVALID_TEACHER_AVAILABILITY"
    assert any(e["code"] == "AVAILABLE_EXCEPTION_MUST_BE_OMITTED" for e in body["errors"])


def test_put_duplicate_cell_rejected(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.put(
        _put_url(problem, "t_math"),
        json={"exceptions": [
            {"day_id": "mon", "period_id": "p5", "status": "UNAVAILABLE"},
            {"day_id": "mon", "period_id": "p5", "status": "PREFER_NOT"},
        ]},
    )
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "INVALID_TEACHER_AVAILABILITY"
    assert any(e["code"] == "DUPLICATE_AVAILABILITY_CELL" for e in body["errors"])


def test_put_unknown_status_rejected(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.put(
        _put_url(problem, "t_math"), json={"exceptions": [{"day_id": "mon", "period_id": "p5", "status": "MAYBE"}]},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "INVALID_TEACHER_AVAILABILITY"


def test_put_missing_teacher_returns_404(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.put(
        _put_url(problem, "t_nobody"), json={"exceptions": [{"day_id": "mon", "period_id": "p5", "status": "UNAVAILABLE"}]},
    )
    assert response.status_code == 404
    assert response.json() == {"detail": "Teacher not found"}


def test_put_unknown_day_returns_422_unknown_reference(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.put(
        _put_url(problem, "t_math"), json={"exceptions": [{"day_id": "someday", "period_id": "p5", "status": "UNAVAILABLE"}]},
    )
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "UNKNOWN_REFERENCE"
    assert body["reference_kind"] == "day"
    assert body["reference_id"] == "someday"


def test_put_unknown_period_returns_422_unknown_reference(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.put(
        _put_url(problem, "t_math"), json={"exceptions": [{"day_id": "mon", "period_id": "p99", "status": "UNAVAILABLE"}]},
    )
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "UNKNOWN_REFERENCE"
    assert body["reference_kind"] == "period"
    assert body["reference_id"] == "p99"


def test_put_locked_returns_409(client, db):
    session, _session_factory = db
    problem = _seed(session)

    generate_response = client.post(f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/generate")
    assert generate_response.status_code == 201

    response = client.put(
        _put_url(problem, "t_math"), json={"exceptions": [{"day_id": "mon", "period_id": "p5", "status": "UNAVAILABLE"}]},
    )
    assert response.status_code == 409
    assert response.json() == {
        "code": "SCHEDULING_CONFIGURATION_LOCKED",
        "detail": "Scheduling configuration is locked because a schedule already exists",
    }


def test_put_no_partial_writes_on_validation_failure(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.put(
        _put_url(problem, "t_math"),
        json={"exceptions": [
            {"day_id": "mon", "period_id": "p5", "status": "UNAVAILABLE"},
            {"day_id": "mon", "period_id": "p5", "status": "PREFER_NOT"},
        ]},
    )
    assert response.status_code == 422

    body = client.get(_get_url(problem)).json()
    assert not any(e["teacher_id"] == "t_math" for e in body["exceptions"])
