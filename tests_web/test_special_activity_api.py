"""Real-PostgreSQL HTTP integration tests for Reserved Activities Slice
A1's `GET/POST .../special-activities` and
`PUT/DELETE .../special-activities/{special_activity_id}`.

Uses the same `join_transaction_mode="create_savepoint"` fixture
pattern as `tests_web/test_subject_api.py`: every adapter-opened
`Session` shares the fixture's single outer, never-committed
`Connection`, so nothing here ever leaves a row in the real database.
The genuine multi-connection concurrency proof (the generation-vs-write
race) lives in `tests_web/test_special_activity_repository.py` -- this
file proves the HTTP contract, not the underlying lock mechanism.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from school_timetable.api.dependencies import (
    get_generate_schedule_service,
    get_schedule_version_repository,
    get_special_activities_projection_service,
    get_special_activity_service,
)
from school_timetable.api.main import app
from school_timetable.application.generate_schedule_service import GenerateScheduleService
from school_timetable.application.special_activity_projection_service import SpecialActivityProjectionService
from school_timetable.application.special_activity_service import SpecialActivityService
from school_timetable.domain.problem import SchedulingProblem
from school_timetable.fixtures.valid_fixture import build_valid_fixture
from school_timetable.persistence import models as m
from school_timetable.persistence.db import get_session
from school_timetable.persistence.problem_repository import SessionFactorySchedulingProblemRepository
from school_timetable.persistence.schedule_repository import SqlAlchemyScheduleVersionRepository
from school_timetable.persistence.special_activity_repository import SqlAlchemySpecialActivityRepository
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

    def override_special_activities_projection_service():
        return SpecialActivityProjectionService(
            SessionFactorySchedulingProblemRepository(session_factory),
            SqlAlchemyScheduleVersionRepository(session_factory),
        )

    def override_special_activity_service():
        return SpecialActivityService(
            SessionFactorySchedulingProblemRepository(session_factory),
            SqlAlchemySpecialActivityRepository(session_factory),
            SqlAlchemyScheduleVersionRepository(session_factory),
        )

    app.dependency_overrides[get_session] = override_get_session
    app.dependency_overrides[get_schedule_version_repository] = override_schedule_repo
    app.dependency_overrides[get_generate_schedule_service] = override_generate_service
    app.dependency_overrides[get_special_activities_projection_service] = (
        override_special_activities_projection_service
    )
    app.dependency_overrides[get_special_activity_service] = override_special_activity_service
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
    app.dependency_overrides.pop(get_special_activities_projection_service, None)
    app.dependency_overrides.pop(get_special_activity_service, None)


def _seed(session: Session) -> SchedulingProblem:
    problem = build_valid_fixture()
    write_scheduling_problem(session, problem)
    session.flush()
    return problem


def _url(problem: SchedulingProblem, *parts: str) -> str:
    return "/".join((f"/schools/{problem.school.id}/years/{problem.academic_year.id}/special-activities", *parts))


def _by_id(items: list[dict], item_id: str) -> dict:
    return next(i for i in items if i["id"] == item_id)


# -- GET --------------------------------------------------------------------

def test_get_exact_response_shape(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.get(_url(problem))
    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"configuration_locked", "special_activities"}
    assert body["configuration_locked"] is False
    item = body["special_activities"][0]
    assert set(item.keys()) == {"id", "name"}


def test_get_club_only_excludes_ordinary(client, db):
    session, _session_factory = db
    problem = _seed(session)

    body = client.get(_url(problem)).json()
    special_activity_ids = {s["id"] for s in body["special_activities"]}
    assert "math" not in special_activity_ids
    assert "science" not in special_activity_ids
    club_activity_ids = [a.id for a in problem.activities if a.kind.value == "CLUB"]
    assert [s["id"] for s in body["special_activities"]] == club_activity_ids


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
    response = client.get("/schools/no-such-school/years/no-such-year/special-activities")
    assert response.status_code == 404
    assert response.json() == {"detail": "Scheduling configuration not found"}


# -- POST -------------------------------------------------------------------

def test_post_create_201_exact_body(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.post(_url(problem), json={"name": "Debate Club"})
    assert response.status_code == 201
    body = response.json()
    assert set(body.keys()) == {"id", "name"}
    assert body["id"].startswith("activity_")
    assert body["name"] == "Debate Club"


def test_post_generated_id_never_accepted_from_body(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.post(
        _url(problem), json={"name": "Debate Club", "id": "activity_client_supplied", "kind": "ORDINARY"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["id"] != "activity_client_supplied"
    assert "kind" not in body


def test_post_persisted_row_kind_is_club(client, db, live_db_engine):
    session, session_factory = db
    problem = _seed(session)

    response = client.post(_url(problem), json={"name": "Debate Club"})
    assert response.status_code == 201
    special_activity_natural_id = response.json()["id"]

    check_session = session_factory()
    year_id = check_session.execute(
        select(m.AcademicYear.id).join(m.School, m.School.id == m.AcademicYear.school_id).where(
            m.School.natural_id == problem.school.id, m.AcademicYear.natural_id == problem.academic_year.id,
        )
    ).scalar_one()
    row = check_session.execute(
        select(m.Activity).where(
            m.Activity.academic_year_id == year_id, m.Activity.natural_id == special_activity_natural_id,
        )
    ).scalar_one()
    assert row.kind == "CLUB"
    check_session.close()


def test_post_stores_trimmed_name(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.post(_url(problem), json={"name": "  Debate Club  "})
    assert response.status_code == 201
    assert response.json()["name"] == "Debate Club"


def test_post_duplicate_other_club_returns_409(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.post(_url(problem), json={"name": "Chess Club"})
    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "DUPLICATE_SPECIAL_ACTIVITY"


def test_post_same_name_as_ordinary_subject_succeeds(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.post(_url(problem), json={"name": "Mathematics"})
    assert response.status_code == 201


def test_post_case_variant_succeeds(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.post(_url(problem), json={"name": "chess club"})
    assert response.status_code == 201


def test_post_blank_name_returns_422(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.post(_url(problem), json={"name": "   "})
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "INVALID_SPECIAL_ACTIVITY"
    assert any(e["code"] == "BLANK_SPECIAL_ACTIVITY_NAME" for e in body["errors"])


def test_post_locked_configuration_returns_409_exact_body(client, db):
    session, _session_factory = db
    problem = _seed(session)

    generate_response = client.post(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/generate"
    )
    assert generate_response.status_code == 201

    response = client.post(_url(problem), json={"name": "Debate Club"})
    assert response.status_code == 409
    assert response.json() == {
        "code": "SCHEDULING_CONFIGURATION_LOCKED",
        "detail": "Scheduling configuration is locked because a schedule already exists",
    }


# -- PUT --------------------------------------------------------------------

def test_put_update_200_preserves_id(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.put(_url(problem, "club_chess"), json={"name": "Strategy Games Club"})
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "club_chess"
    assert body["name"] == "Strategy Games Club"


def test_put_not_found_returns_404(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.put(_url(problem, "does-not-exist"), json={"name": "X"})
    assert response.status_code == 404
    assert response.json() == {"detail": "Special activity not found"}


def test_put_ordinary_id_returns_404_and_subject_unchanged(client, db, live_db_engine):
    session, session_factory = db
    problem = _seed(session)

    response = client.put(_url(problem, "math"), json={"name": "Renamed"})
    assert response.status_code == 404
    assert response.json() == {"detail": "Special activity not found"}

    check_session = session_factory()
    year_id = check_session.execute(
        select(m.AcademicYear.id).join(m.School, m.School.id == m.AcademicYear.school_id).where(
            m.School.natural_id == problem.school.id, m.AcademicYear.natural_id == problem.academic_year.id,
        )
    ).scalar_one()
    row = check_session.execute(
        select(m.Activity).where(m.Activity.academic_year_id == year_id, m.Activity.natural_id == "math")
    ).scalar_one()
    assert row.name == "Mathematics"
    assert row.kind == "ORDINARY"
    check_session.close()


def test_put_duplicate_other_club_returns_409(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.put(_url(problem, "club_chess"), json={"name": "Robotics Club"})
    assert response.status_code == 409
    assert response.json()["code"] == "DUPLICATE_SPECIAL_ACTIVITY"


def test_put_locked_configuration_returns_409(client, db):
    session, _session_factory = db
    problem = _seed(session)

    generate_response = client.post(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/generate"
    )
    assert generate_response.status_code == 201

    response = client.put(_url(problem, "club_chess"), json={"name": "Renamed"})
    assert response.status_code == 409
    assert response.json()["code"] == "SCHEDULING_CONFIGURATION_LOCKED"


def test_put_rename_synchronizes_reserved_block_name_in_config(client, db):
    """The mandatory required regression (spec section 25/10): renaming
    a Special Activity referenced by ReservedBlock(s) atomically
    synchronizes every referencing block's own persisted `name`, and
    `/config` reflects it immediately -- an unrelated block for another
    Activity is left untouched."""
    session, _session_factory = db
    problem = _seed(session)

    response = client.put(_url(problem, "club_chess"), json={"name": "STEM Lab"})
    assert response.status_code == 200

    special_activities_body = client.get(_url(problem)).json()
    assert _by_id(special_activities_body["special_activities"], "club_chess")["name"] == "STEM Lab"

    config_body = client.get(f"/schools/{problem.school.id}/years/{problem.academic_year.id}/config").json()
    config_activity = _by_id(config_body["activities"], "club_chess")
    assert config_activity["name"] == "STEM Lab"
    assert config_activity["kind"] == "CLUB"

    reserved_blocks = config_body["reserved_blocks"]
    chess_block = next(b for b in reserved_blocks if b["activity_id"] == "club_chess")
    assert chess_block["name"] == "STEM Lab"
    robotics_block = next(b for b in reserved_blocks if b["activity_id"] == "club_robotics")
    assert robotics_block["name"] == "Robotics Club"


# -- DELETE -------------------------------------------------------------

def test_delete_unused_club_200_exact_body(client, db):
    session, _session_factory = db
    problem = _seed(session)

    create_response = client.post(_url(problem), json={"name": "Debate Club"})
    new_id = create_response.json()["id"]

    response = client.delete(_url(problem, new_id))
    assert response.status_code == 200
    assert response.json() == {"deleted_id": new_id}

    body = client.get(_url(problem)).json()
    assert new_id not in {s["id"] for s in body["special_activities"]}


def test_delete_not_found_returns_404(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.delete(_url(problem, "does-not-exist"))
    assert response.status_code == 404
    assert response.json() == {"detail": "Special activity not found"}


def test_delete_ordinary_id_returns_404_and_subject_remains(client, db, live_db_engine):
    session, session_factory = db
    problem = _seed(session)

    response = client.delete(_url(problem, "math"))
    assert response.status_code == 404
    assert response.json() == {"detail": "Special activity not found"}

    check_session = session_factory()
    year_id = check_session.execute(
        select(m.AcademicYear.id).join(m.School, m.School.id == m.AcademicYear.school_id).where(
            m.School.natural_id == problem.school.id, m.AcademicYear.natural_id == problem.academic_year.id,
        )
    ).scalar_one()
    row = check_session.execute(
        select(m.Activity).where(m.Activity.academic_year_id == year_id, m.Activity.natural_id == "math")
    ).scalar_one_or_none()
    assert row is not None
    check_session.close()


def test_delete_reserved_block_reference_returns_409(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.delete(_url(problem, "club_chess"))
    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "SPECIAL_ACTIVITY_IN_USE"
    assert "RESERVED_BLOCK" in body["referenced_by"]

    get_body = client.get(_url(problem)).json()
    assert "club_chess" in {s["id"] for s in get_body["special_activities"]}


def test_delete_locked_configuration_returns_409(client, db):
    session, _session_factory = db
    problem = _seed(session)

    generate_response = client.post(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/generate"
    )
    assert generate_response.status_code == 201

    # The fast lock precheck runs before any existence check, so this
    # rejects with the lock error regardless of "club_chess" being referenced.
    response = client.delete(_url(problem, "club_chess"))
    assert response.status_code == 409
    assert response.json()["code"] == "SCHEDULING_CONFIGURATION_LOCKED"


# -- INTEROPERABILITY: cross-flow visibility ---------------------------------

def test_create_visible_across_config(client, db):
    session, _session_factory = db
    problem = _seed(session)

    create_response = client.post(_url(problem), json={"name": "Debate Club"})
    assert create_response.status_code == 201
    new_id = create_response.json()["id"]

    special_activities_body = client.get(_url(problem)).json()
    created = _by_id(special_activities_body["special_activities"], new_id)
    assert created["name"] == "Debate Club"

    config_body = client.get(f"/schools/{problem.school.id}/years/{problem.academic_year.id}/config").json()
    config_activity = _by_id(config_body["activities"], new_id)
    assert config_activity["name"] == "Debate Club"
    assert config_activity["kind"] == "CLUB"
