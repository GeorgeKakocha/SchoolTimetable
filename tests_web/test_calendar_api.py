"""Real-PostgreSQL HTTP integration tests for Calendar A's
`GET .../calendar`, `POST/PUT/DELETE .../calendar/days[/{day_id}]`,
`POST .../calendar/days/{day_id}/move`, and the Period equivalents.

Uses the same `join_transaction_mode="create_savepoint"` fixture
pattern as `tests_web/test_resource_api.py`: every adapter-opened
`Session` shares the fixture's single outer, never-committed
`Connection`, so nothing here ever leaves a row in the real database.
"""
from __future__ import annotations

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from school_timetable.api.dependencies import (
    get_calendar_projection_service,
    get_calendar_service,
    get_generate_schedule_service,
    get_schedule_version_repository,
)
from school_timetable.api.main import app
from school_timetable.application.calendar_projection_service import CalendarProjectionService
from school_timetable.application.calendar_service import CalendarService
from school_timetable.application.generate_schedule_service import GenerateScheduleService
from school_timetable.domain.problem import SchedulingProblem
from school_timetable.fixtures.valid_fixture import build_valid_fixture
from school_timetable.persistence.calendar_repository import (
    SqlAlchemyCalendarDayRepository,
    SqlAlchemyCalendarPeriodRepository,
)
from school_timetable.persistence.db import get_session
from school_timetable.persistence.problem_repository import SessionFactorySchedulingProblemRepository
from school_timetable.persistence.schedule_repository import SqlAlchemyScheduleVersionRepository
from tests_web.support.problem_writer import write_scheduling_problem


def _fixture_without_time_preferences():
    problem = build_valid_fixture()
    return replace(
        problem,
        teaching_requirements=tuple(replace(r, time_preferences=()) for r in problem.teaching_requirements),
    )


@pytest.fixture
def db(live_db_engine):
    connection = live_db_engine.connect()
    transaction = connection.begin()
    session_factory = sessionmaker(
        bind=connection, autoflush=False, autocommit=False, expire_on_commit=False,
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

    def override_calendar_projection_service():
        return CalendarProjectionService(
            SessionFactorySchedulingProblemRepository(session_factory),
            SqlAlchemyScheduleVersionRepository(session_factory),
        )

    def override_calendar_service():
        return CalendarService(
            SessionFactorySchedulingProblemRepository(session_factory),
            SqlAlchemyCalendarDayRepository(session_factory),
            SqlAlchemyCalendarPeriodRepository(session_factory),
            SqlAlchemyScheduleVersionRepository(session_factory),
        )

    app.dependency_overrides[get_session] = override_get_session
    app.dependency_overrides[get_schedule_version_repository] = override_schedule_repo
    app.dependency_overrides[get_generate_schedule_service] = override_generate_service
    app.dependency_overrides[get_calendar_projection_service] = override_calendar_projection_service
    app.dependency_overrides[get_calendar_service] = override_calendar_service
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
    app.dependency_overrides.pop(get_calendar_projection_service, None)
    app.dependency_overrides.pop(get_calendar_service, None)


def _seed(session: Session, problem: SchedulingProblem | None = None) -> SchedulingProblem:
    problem = problem if problem is not None else _fixture_without_time_preferences()
    write_scheduling_problem(session, problem)
    session.flush()
    return problem


def _url(problem: SchedulingProblem, *parts: str) -> str:
    return "/".join((f"/schools/{problem.school.id}/years/{problem.academic_year.id}/calendar", *parts))


def _config_url(problem: SchedulingProblem) -> str:
    return f"/schools/{problem.school.id}/years/{problem.academic_year.id}/config"


def _by_id(items: list[dict], item_id: str) -> dict:
    return next(i for i in items if i["id"] == item_id)


# -- GET ----------------------------------------------------------------

def test_get_exact_response_shape(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.get(_url(problem))
    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"configuration_locked", "days", "periods"}
    assert body["configuration_locked"] is False
    assert set(body["days"][0].keys()) == {"id", "name", "index"}
    assert set(body["periods"][0].keys()) == {
        "id", "name", "index", "start_time", "end_time", "starts_new_block", "is_instructional",
    }


def test_get_days_ordered_by_index(client, db):
    session, _session_factory = db
    problem = _seed(session)

    body = client.get(_url(problem)).json()
    assert [d["id"] for d in body["days"]] == ["mon", "tue", "wed", "thu", "fri"]


def test_get_periods_ordered_by_index_and_first_starts_new_block(client, db):
    session, _session_factory = db
    problem = _seed(session)

    body = client.get(_url(problem)).json()
    assert [p["id"] for p in body["periods"]] == [f"p{i}" for i in range(1, 9)]
    assert body["periods"][0]["starts_new_block"] is True


def test_get_never_exposes_raw_block_id(client, db):
    session, _session_factory = db
    problem = _seed(session)

    body = client.get(_url(problem)).json()
    for period in body["periods"]:
        assert "block_id" not in period


def test_get_null_clock_times_serialize_as_null(client, db):
    session, _session_factory = db
    problem = _seed(session)

    body = client.get(_url(problem)).json()
    assert body["periods"][0]["start_time"] is None
    assert body["periods"][0]["end_time"] is None


def test_get_configuration_locked_true_after_generate(client, db):
    session, _session_factory = db
    problem = _seed(session)

    generate_response = client.post(f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/generate")
    assert generate_response.status_code == 201

    body = client.get(_url(problem)).json()
    assert body["configuration_locked"] is True


def test_get_unknown_school_year_returns_404(client):
    response = client.get("/schools/no-such-school/years/no-such-year/calendar")
    assert response.status_code == 404
    assert response.json() == {"detail": "Scheduling configuration not found"}


def test_config_endpoint_still_includes_period_hhmm_fields_backward_compatible(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.get(_config_url(problem))
    assert response.status_code == 200
    body = response.json()
    period = body["periods"][0]
    assert "start_time" in period and "end_time" in period
    assert period["start_time"] is None


# -- Day POST -------------------------------------------------------------

def test_post_day_create_201(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.post(_url(problem, "days"), json={"name": "Saturday"})
    assert response.status_code == 201
    body = response.json()
    assert set(body.keys()) == {"id", "name", "index"}
    assert body["name"] == "Saturday"
    assert body["index"] == 5


def test_post_day_blank_name_returns_422(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.post(_url(problem, "days"), json={"name": "   "})
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "INVALID_DAY"
    assert any(e["code"] == "BLANK_DAY_NAME" for e in body["errors"])


def test_post_day_duplicate_name_returns_409(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.post(_url(problem, "days"), json={"name": "Monday"})
    assert response.status_code == 409
    assert response.json()["code"] == "DUPLICATE_DAY"


def test_post_day_locked_configuration_returns_409_exact_body(client, db):
    session, _session_factory = db
    problem = _seed(session)

    generate_response = client.post(f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/generate")
    assert generate_response.status_code == 201

    response = client.post(_url(problem, "days"), json={"name": "Saturday"})
    assert response.status_code == 409
    assert response.json() == {
        "code": "SCHEDULING_CONFIGURATION_LOCKED",
        "detail": "Scheduling configuration is locked because a schedule already exists",
    }


# -- Day PUT ----------------------------------------------------------------

def test_put_day_update_200(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.put(_url(problem, "days", "mon"), json={"name": "Renamed Monday"})
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "mon"
    assert body["name"] == "Renamed Monday"


def test_put_day_not_found_returns_404(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.put(_url(problem, "days", "does-not-exist"), json={"name": "X"})
    assert response.status_code == 404
    assert response.json() == {"detail": "Day not found"}


# -- Day DELETE ---------------------------------------------------------------

def test_delete_day_200(client, db):
    session, _session_factory = db
    problem = _seed(session)

    create_response = client.post(_url(problem, "days"), json={"name": "Saturday"})
    day_id = create_response.json()["id"]

    response = client.delete(_url(problem, "days", day_id))
    assert response.status_code == 200
    assert response.json() == {"deleted_id": day_id}


def test_delete_day_in_use_returns_409(client, db):
    session, _session_factory = db
    problem = _seed(session)

    # "tue" is referenced by a PREFER_NOT TeacherAvailability row.
    response = client.delete(_url(problem, "days", "tue"))
    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "DAY_IN_USE"
    assert "TEACHER_AVAILABILITY" in body["referenced_by"]


def test_delete_final_day_returns_422(client, db):
    # A dedicated minimal, reference-free 2-Day problem -- the shared
    # fixture's own Days are all referenced by something (teacher
    # availability/reserved blocks/fixed placement), so deleting down to
    # one there would hit DAY_IN_USE (409) before ever reaching the
    # minimum-calendar rule this test targets.
    session, _session_factory = db
    from school_timetable.domain.calendar import AcademicYear, Day as DomainDay
    from school_timetable.domain.problem import SchedulingProblem
    from school_timetable.domain.school import School

    problem = SchedulingProblem(
        school=School(id="minimal-school-day-delete", name="Minimal School"),
        academic_year=AcademicYear(id="minimal-year", label="Minimal Year"),
        days=(DomainDay(id="d1", name="Day 1", index=0), DomainDay(id="d2", name="Day 2", index=1)),
        periods=(),
        teachers=(), class_sections=(), participant_groups=(), activities=(),
        teaching_requirements=(),
    )
    _seed(session, problem)

    delete_response = client.delete(_url(problem, "days", "d2"))
    assert delete_response.status_code == 200

    response = client.delete(_url(problem, "days", "d1"))
    assert response.status_code == 422
    assert response.json()["code"] == "INVALID_DAY"


# -- Day move -----------------------------------------------------------------

def test_post_day_move_returns_full_projection(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.post(_url(problem, "days", "tue", "move"), json={"direction": "up"})
    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"configuration_locked", "days", "periods"}
    by_id = {d["id"]: d["index"] for d in body["days"]}
    assert by_id["tue"] == 0
    assert by_id["mon"] == 1


def test_post_day_move_at_edge_returns_422(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.post(_url(problem, "days", "mon", "move"), json={"direction": "up"})
    assert response.status_code == 422
    assert response.json()["code"] == "INVALID_DAY"


# -- Period POST --------------------------------------------------------------

def test_post_period_create_201(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.post(_url(problem, "periods"), json={"name": "Period 9"})
    assert response.status_code == 201
    body = response.json()
    assert set(body.keys()) == {"id", "name", "index", "start_time", "end_time", "starts_new_block", "is_instructional"}
    assert body["is_instructional"] is True
    assert body["index"] == 8


def test_post_period_create_with_clock_times(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.post(
        _url(problem, "periods"),
        json={"name": "Period 9", "start_time": "15:00", "end_time": "15:45", "starts_new_block": True},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["start_time"] == "15:00"
    assert body["end_time"] == "15:45"
    assert body["starts_new_block"] is True


def test_post_period_invalid_time_format_returns_422(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.post(
        _url(problem, "periods"), json={"name": "Period 9", "start_time": "25:99", "end_time": "15:45"},
    )
    assert response.status_code == 422
    assert response.json()["code"] == "INVALID_PERIOD"


def test_post_period_only_start_time_returns_422(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.post(_url(problem, "periods"), json={"name": "Period 9", "start_time": "09:00"})
    assert response.status_code == 422
    body = response.json()
    assert any(e["code"] == "PERIOD_TIME_PAIR_INCOMPLETE" for e in body["errors"])


def test_post_period_blank_name_returns_422(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.post(_url(problem, "periods"), json={"name": "  "})
    assert response.status_code == 422
    assert response.json()["code"] == "INVALID_PERIOD"


def test_post_period_duplicate_name_returns_409(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.post(_url(problem, "periods"), json={"name": "Period 1"})
    assert response.status_code == 409
    assert response.json()["code"] == "DUPLICATE_PERIOD"


def test_post_period_is_instructional_not_accepted_as_client_field(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.post(
        _url(problem, "periods"), json={"name": "Period 9", "is_instructional": False},
    )
    assert response.status_code == 201
    assert response.json()["is_instructional"] is True


# -- Period PUT ---------------------------------------------------------------

def test_put_period_update_200_preserves_is_instructional(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.put(_url(problem, "periods", "p1"), json={"name": "Renamed Period 1"})
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Renamed Period 1"
    assert body["is_instructional"] is True


def test_put_period_not_found_returns_404(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.put(_url(problem, "periods", "does-not-exist"), json={"name": "X"})
    assert response.status_code == 404
    assert response.json() == {"detail": "Period not found"}


def test_put_period_clock_overlap_returns_422(client, db):
    session, _session_factory = db
    problem = _seed(session)

    client.put(_url(problem, "periods", "p2"), json={"name": "Period 2", "start_time": "09:00", "end_time": "09:45"})
    response = client.put(
        _url(problem, "periods", "p1"), json={"name": "Period 1", "start_time": "09:15", "end_time": "10:00"},
    )
    assert response.status_code == 422
    body = response.json()
    assert any(e["code"] == "PERIOD_CLOCK_TIME_OVERLAP" for e in body["errors"])


# -- Period DELETE ------------------------------------------------------------

def test_delete_period_200(client, db):
    session, _session_factory = db
    problem = _seed(session)

    create_response = client.post(_url(problem, "periods"), json={"name": "Period 9"})
    period_id = create_response.json()["id"]

    response = client.delete(_url(problem, "periods", period_id))
    assert response.status_code == 200
    assert response.json() == {"deleted_id": period_id}


def test_delete_period_in_use_returns_409(client, db):
    session, _session_factory = db
    problem = _seed(session)

    # p7 is unavailable for t_science in the fixture.
    response = client.delete(_url(problem, "periods", "p7"))
    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "PERIOD_IN_USE"
    assert "TEACHER_AVAILABILITY" in body["referenced_by"]


def test_delete_period_blocked_by_time_preference_returns_409(client, db):
    session, _session_factory = db
    # This test needs the fixture's OWN TimePreference (preferred_periods
    # (0, 1) on history_8a), so seed the unmodified fixture directly.
    problem = _seed(session, build_valid_fixture())

    response = client.delete(_url(problem, "periods", "p1"))
    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "PERIOD_IN_USE"
    assert "TIME_PREFERENCE" in body["referenced_by"]


# -- Period move ----------------------------------------------------------

def test_post_period_move_returns_full_projection_with_recomputed_blocks(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.post(_url(problem, "periods", "p4", "move"), json={"direction": "down"})
    assert response.status_code == 200
    body = response.json()
    by_id = {p["id"]: p for p in body["periods"]}
    assert by_id["p4"]["index"] == 4
    assert by_id["p5"]["index"] == 3


def test_post_period_move_blocked_by_time_preference_returns_409(client, db):
    session, _session_factory = db
    problem = _seed(session, build_valid_fixture())

    response = client.post(_url(problem, "periods", "p8", "move"), json={"direction": "up"})
    assert response.status_code == 409
    assert response.json()["code"] == "PERIOD_REORDER_BLOCKED_BY_TIME_PREFERENCES"


def test_post_period_move_at_edge_returns_422(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.post(_url(problem, "periods", "p1", "move"), json={"direction": "up"})
    assert response.status_code == 422
    assert response.json()["code"] == "INVALID_PERIOD"
