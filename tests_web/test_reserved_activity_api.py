"""Real-PostgreSQL HTTP integration tests for Reserved Activities Slice
A2's `GET/POST .../reserved-activities` and
`PUT/DELETE .../reserved-activities/{reserved_activity_id}`.

Uses the same `join_transaction_mode="create_savepoint"` fixture
pattern as `tests_web/test_special_activity_api.py`: every
adapter-opened `Session` shares the fixture's single outer,
never-committed `Connection`, so nothing here ever leaves a row in the
real database. The genuine multi-connection concurrency proof (the
generation-vs-write race) lives in
`tests_web/test_reserved_activity_repository.py` -- this file proves
the HTTP contract, not the underlying lock mechanism.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from school_timetable.api.dependencies import (
    get_generate_schedule_service,
    get_reserved_activities_projection_service,
    get_reserved_activity_service,
    get_schedule_version_repository,
)
from school_timetable.api.main import app
from school_timetable.application.generate_schedule_service import GenerateScheduleService
from school_timetable.application.reserved_activity_projection_service import ReservedActivityProjectionService
from school_timetable.application.reserved_activity_service import ReservedActivityService
from school_timetable.domain.problem import SchedulingProblem
from school_timetable.fixtures.valid_fixture import build_valid_fixture
from school_timetable.persistence.problem_repository import SessionFactorySchedulingProblemRepository
from school_timetable.persistence.reserved_activity_repository import SqlAlchemyReservedActivityRepository
from school_timetable.persistence.db import get_session
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

    def override_reserved_activities_projection_service():
        return ReservedActivityProjectionService(
            SessionFactorySchedulingProblemRepository(session_factory),
            SqlAlchemyScheduleVersionRepository(session_factory),
        )

    def override_reserved_activity_service():
        return ReservedActivityService(
            SessionFactorySchedulingProblemRepository(session_factory),
            SqlAlchemyReservedActivityRepository(session_factory),
            SqlAlchemyScheduleVersionRepository(session_factory),
        )

    app.dependency_overrides[get_session] = override_get_session
    app.dependency_overrides[get_schedule_version_repository] = override_schedule_repo
    app.dependency_overrides[get_generate_schedule_service] = override_generate_service
    app.dependency_overrides[get_reserved_activities_projection_service] = (
        override_reserved_activities_projection_service
    )
    app.dependency_overrides[get_reserved_activity_service] = override_reserved_activity_service
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
    app.dependency_overrides.pop(get_reserved_activities_projection_service, None)
    app.dependency_overrides.pop(get_reserved_activity_service, None)


def _seed(session: Session) -> SchedulingProblem:
    problem = build_valid_fixture()
    write_scheduling_problem(session, problem)
    session.flush()
    return problem


def _url(problem: SchedulingProblem, *parts: str) -> str:
    return "/".join((f"/schools/{problem.school.id}/years/{problem.academic_year.id}/reserved-activities", *parts))


def _by_id(items: list[dict], item_id: str) -> dict:
    return next(i for i in items if i["id"] == item_id)


def _body(special_activity_id="club_robotics", class_section_ids=("8a",), teacher_id=None, slots=(("mon", "p1"),)):
    return {
        "special_activity_id": special_activity_id,
        "class_section_ids": list(class_section_ids),
        "teacher_id": teacher_id,
        "slots": [{"day_id": d, "period_id": p} for d, p in slots],
    }


def _minimal_problem_with_non_instructional_period() -> SchedulingProblem:
    from school_timetable.domain.activities import Activity, ActivityKind
    from school_timetable.domain.calendar import AcademicYear, Day, Period
    from school_timetable.domain.groups import ClassSection, ParticipantGroup, ParticipantGroupRole
    from school_timetable.domain.school import School

    return SchedulingProblem(
        school=School(id="reserved-activity-non-instr-school", name="Non-Instructional Test School"),
        academic_year=AcademicYear(id="ay-non-instr", label="2026/2027"),
        days=(Day(id="d1", name="D1", index=0),),
        periods=(
            Period(id="p1", name="P1", index=0, block_id="blk", is_instructional=True),
            Period(id="p2", name="P2", index=1, block_id="blk", is_instructional=False),
        ),
        teachers=(),
        class_sections=(ClassSection(id="c1", name="C1"),),
        participant_groups=(
            ParticipantGroup(id="pg1", name="PG1", class_sections=("c1",), role=ParticipantGroupRole.WHOLE_CLASS),
        ),
        activities=(Activity(id="club1", name="Club1", kind=ActivityKind.CLUB),),
        teaching_requirements=(),
    )


# -- GET --------------------------------------------------------------------

def test_get_exact_top_level_shape(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.get(_url(problem))
    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {
        "configuration_locked", "special_activities", "teachers", "class_sections", "days", "periods",
        "reserved_activities",
    }


def test_get_configuration_locked_false_initially(client, db):
    session, _session_factory = db
    problem = _seed(session)
    body = client.get(_url(problem)).json()
    assert body["configuration_locked"] is False


def test_get_special_activities_club_only(client, db):
    session, _session_factory = db
    problem = _seed(session)
    body = client.get(_url(problem)).json()
    ids = [a["id"] for a in body["special_activities"]]
    assert set(ids) == {"club_chess", "club_robotics"}
    assert "math" not in ids


def test_get_teacher_list(client, db):
    session, _session_factory = db
    problem = _seed(session)
    body = client.get(_url(problem)).json()
    assert len(body["teachers"]) == len(problem.teachers)
    assert set(t["id"] for t in body["teachers"]) == {t.id for t in problem.teachers}


def test_get_class_section_list(client, db):
    session, _session_factory = db
    problem = _seed(session)
    body = client.get(_url(problem)).json()
    assert set(c["id"] for c in body["class_sections"]) == {c.id for c in problem.class_sections}


def test_get_all_periods_with_is_instructional(client, db):
    session, _session_factory = db
    problem = _seed(session)
    body = client.get(_url(problem)).json()
    assert len(body["periods"]) == len(problem.periods)
    assert all("is_instructional" in p for p in body["periods"])
    assert all(p["is_instructional"] is True for p in body["periods"])


def test_get_reserved_activity_exact_normalized_item(client, db):
    session, _session_factory = db
    problem = _seed(session)
    body = client.get(_url(problem)).json()
    item = _by_id(body["reserved_activities"], "club_chess")
    assert set(item.keys()) == {"id", "special_activity_id", "class_section_ids", "teacher_id", "slots"}
    assert item["special_activity_id"] == "club_chess"
    assert item["class_section_ids"] == ["8a", "8b"]
    assert item["teacher_id"] is None
    assert item["slots"] == [{"day_id": "wed", "period_id": "p8"}]


def test_get_reserved_activity_item_never_leaks_names_kind_ordinal(client, db):
    session, _session_factory = db
    problem = _seed(session)
    body = client.get(_url(problem)).json()
    raw = client.get(_url(problem)).text
    assert "special_activity_name" not in raw
    assert "teacher_name" not in raw
    for item in body["reserved_activities"]:
        assert "name" not in item
        assert "kind" not in item
        assert "ordinal" not in item


def test_get_unknown_school_year_returns_404(client):
    response = client.get("/schools/no-such-school/years/no-such-year/reserved-activities")
    assert response.status_code == 404
    assert response.json() == {"detail": "Scheduling configuration not found"}


# -- POST -------------------------------------------------------------------

def test_post_create_201_exact_normalized_response(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.post(_url(problem), json=_body())
    assert response.status_code == 201
    body = response.json()
    assert set(body.keys()) == {"id", "special_activity_id", "class_section_ids", "teacher_id", "slots"}
    assert body["id"].startswith("reserved_block_")
    assert body["special_activity_id"] == "club_robotics"
    assert body["class_section_ids"] == ["8a"]
    assert body["teacher_id"] is None
    assert body["slots"] == [{"day_id": "mon", "period_id": "p1"}]


def test_post_canonical_class_order(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.post(_url(problem), json=_body(class_section_ids=("9b", "8a")))
    assert response.status_code == 201
    assert response.json()["class_section_ids"] == ["8a", "9b"]


def test_post_canonical_slot_order(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.post(_url(problem), json=_body(slots=(("fri", "p2"), ("mon", "p1"))))
    assert response.status_code == 201
    assert response.json()["slots"] == [{"day_id": "mon", "period_id": "p1"}, {"day_id": "fri", "period_id": "p2"}]


def test_post_empty_class_list_returns_422(client, db):
    session, _session_factory = db
    problem = _seed(session)
    body = _body()
    body["class_section_ids"] = []
    response = client.post(_url(problem), json=body)
    assert response.status_code == 422
    payload = response.json()
    assert payload["code"] == "INVALID_RESERVED_ACTIVITY"
    assert any(e["code"] == "RESERVED_BLOCK_REQUIRES_CLASS_SECTION" for e in payload["errors"])


def test_post_empty_slots_returns_422(client, db):
    session, _session_factory = db
    problem = _seed(session)
    body = _body()
    body["slots"] = []
    response = client.post(_url(problem), json=body)
    assert response.status_code == 422
    payload = response.json()
    assert any(e["code"] == "RESERVED_BLOCK_REQUIRES_SLOT" for e in payload["errors"])


def test_post_duplicate_class_in_request_returns_422(client, db):
    session, _session_factory = db
    problem = _seed(session)
    response = client.post(_url(problem), json=_body(class_section_ids=("8a", "8a")))
    assert response.status_code == 422
    payload = response.json()
    assert any(e["code"] == "DUPLICATE_RESERVED_BLOCK_CLASS_SECTION" for e in payload["errors"])


def test_post_duplicate_slot_in_request_returns_422(client, db):
    session, _session_factory = db
    problem = _seed(session)
    response = client.post(_url(problem), json=_body(slots=(("mon", "p1"), ("mon", "p1"))))
    assert response.status_code == 422
    payload = response.json()
    assert any(e["code"] == "DUPLICATE_RESERVED_BLOCK_SLOT" for e in payload["errors"])


def test_post_unknown_special_activity_returns_422(client, db):
    session, _session_factory = db
    problem = _seed(session)
    response = client.post(_url(problem), json=_body(special_activity_id="no-such-activity"))
    assert response.status_code == 422
    payload = response.json()
    assert payload["code"] == "UNKNOWN_REFERENCE"
    assert payload["reference_kind"] == "special_activity"


def test_post_unknown_class_returns_422(client, db):
    session, _session_factory = db
    problem = _seed(session)
    response = client.post(_url(problem), json=_body(class_section_ids=("no-such-class",)))
    assert response.status_code == 422
    assert response.json()["reference_kind"] == "class_section"


def test_post_unknown_teacher_returns_422(client, db):
    session, _session_factory = db
    problem = _seed(session)
    response = client.post(_url(problem), json=_body(teacher_id="no-such-teacher"))
    assert response.status_code == 422
    assert response.json()["reference_kind"] == "teacher"


def test_post_unknown_day_returns_422(client, db):
    session, _session_factory = db
    problem = _seed(session)
    response = client.post(_url(problem), json=_body(slots=(("no-such-day", "p1"),)))
    assert response.status_code == 422
    assert response.json()["reference_kind"] == "day"


def test_post_unknown_period_returns_422(client, db):
    session, _session_factory = db
    problem = _seed(session)
    response = client.post(_url(problem), json=_body(slots=(("mon", "no-such-period"),)))
    assert response.status_code == 422
    assert response.json()["reference_kind"] == "period"


def test_post_wrong_kind_returns_exact_non_special_activity_target_body(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.post(_url(problem), json=_body(special_activity_id="math"))
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "NON_SPECIAL_ACTIVITY_TARGET"
    assert body["detail"] == "Selected activity is not a Special Activity."
    assert body["activity_id"] == "math"
    assert "actual_kind" not in body
    assert "kind" not in body
    raw = response.text
    assert "CLUB" not in raw
    assert "ORDINARY" not in raw


def test_post_non_instructional_slot_returns_422(client, db):
    # The shared pilot fixture has no non-instructional period, so this
    # seeds a small dedicated problem (one CLUB activity, one class,
    # one instructional + one non-instructional period) directly,
    # rather than skipping real end-to-end API coverage of this
    # invariant.
    session, _session_factory = db
    problem = _minimal_problem_with_non_instructional_period()
    write_scheduling_problem(session, problem)
    session.flush()

    response = client.post(
        _url(problem), json=_body(special_activity_id="club1", class_section_ids=("c1",), slots=(("d1", "p2"),)),
    )
    assert response.status_code == 422
    payload = response.json()
    assert payload["code"] == "INVALID_RESERVED_ACTIVITY"
    assert any(e["code"] == "RESERVED_BLOCK_NON_INSTRUCTIONAL_SLOT" for e in payload["errors"])


def test_post_teacher_unavailable_returns_422(client, db):
    session, _session_factory = db
    problem = _seed(session)
    response = client.post(_url(problem), json=_body(teacher_id="t_science", slots=(("fri", "p7"),)))
    assert response.status_code == 422
    payload = response.json()
    assert payload["code"] == "INVALID_RESERVED_ACTIVITY"
    assert any(e["code"] == "RESERVED_BLOCK_TEACHER_UNAVAILABLE" for e in payload["errors"])


def test_post_class_collision_returns_422(client, db):
    session, _session_factory = db
    problem = _seed(session)
    response = client.post(_url(problem), json=_body(class_section_ids=("8a",), slots=(("wed", "p8"),)))
    assert response.status_code == 422
    payload = response.json()
    assert any(e["code"] == "RESERVED_BLOCK_CLASS_SLOT_COLLISION" for e in payload["errors"])


def test_post_teacher_collision_returns_422(client, db):
    session, _session_factory = db
    problem = _seed(session)
    first = client.post(_url(problem), json=_body(class_section_ids=("9a",), teacher_id="t_art", slots=(("thu", "p1"),)))
    assert first.status_code == 201

    second = client.post(_url(problem), json=_body(class_section_ids=("9b",), teacher_id="t_art", slots=(("thu", "p1"),)))
    assert second.status_code == 422
    payload = second.json()
    assert any(e["code"] == "RESERVED_BLOCK_TEACHER_SLOT_COLLISION" for e in payload["errors"])


def test_post_locked_returns_409(client, db):
    session, _session_factory = db
    problem = _seed(session)
    generate_response = client.post(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/generate"
    )
    assert generate_response.status_code == 201

    response = client.post(_url(problem), json=_body())
    assert response.status_code == 409
    assert response.json() == {
        "code": "SCHEDULING_CONFIGURATION_LOCKED",
        "detail": "Scheduling configuration is locked because a schedule already exists",
    }


# -- PUT --------------------------------------------------------------------

def test_put_whole_replacement_200_exact_response(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.put(
        _url(problem, "club_chess"),
        json=_body(special_activity_id="club_chess", class_section_ids=("9a", "9b"), slots=(("thu", "p1"),)),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "club_chess"
    assert body["class_section_ids"] == ["9a", "9b"]
    assert body["slots"] == [{"day_id": "thu", "period_id": "p1"}]


def test_put_not_found_returns_404(client, db):
    session, _session_factory = db
    problem = _seed(session)
    response = client.put(_url(problem, "no-such-block"), json=_body())
    assert response.status_code == 404
    assert response.json() == {"detail": "Reserved activity not found"}


def test_put_nested_missing_special_activity_returns_422_unknown_reference(client, db):
    session, _session_factory = db
    problem = _seed(session)
    response = client.put(_url(problem, "club_chess"), json=_body(special_activity_id="no-such-activity"))
    assert response.status_code == 422
    assert response.json()["code"] == "UNKNOWN_REFERENCE"


def test_put_nested_wrong_kind_returns_non_special_activity_target(client, db):
    session, _session_factory = db
    problem = _seed(session)
    response = client.put(_url(problem, "club_chess"), json=_body(special_activity_id="math"))
    assert response.status_code == 422
    assert response.json()["code"] == "NON_SPECIAL_ACTIVITY_TARGET"


def test_put_locked_returns_409(client, db):
    session, _session_factory = db
    problem = _seed(session)
    generate_response = client.post(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/generate"
    )
    assert generate_response.status_code == 201

    response = client.put(_url(problem, "club_chess"), json=_body())
    assert response.status_code == 409
    assert response.json()["code"] == "SCHEDULING_CONFIGURATION_LOCKED"


def test_put_rename_visible_in_config_response(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.put(
        _url(problem, "club_chess"),
        json=_body(special_activity_id="club_robotics", class_section_ids=("8a", "8b"), slots=(("wed", "p8"),)),
    )
    assert response.status_code == 200

    config_body = client.get(f"/schools/{problem.school.id}/years/{problem.academic_year.id}/config").json()
    reserved = _by_id(config_body["reserved_blocks"], "club_chess")
    assert reserved["activity_id"] == "club_robotics"
    assert reserved["name"] == "Robotics Club"


# -- DELETE -------------------------------------------------------------

def test_delete_200_exact_body(client, db):
    session, _session_factory = db
    problem = _seed(session)

    create_response = client.post(_url(problem), json=_body(class_section_ids=("9a",), slots=(("thu", "p2"),)))
    new_id = create_response.json()["id"]

    response = client.delete(_url(problem, new_id))
    assert response.status_code == 200
    assert response.json() == {"deleted_id": new_id}

    body = client.get(_url(problem)).json()
    assert new_id not in {r["id"] for r in body["reserved_activities"]}


def test_delete_not_found_returns_404(client, db):
    session, _session_factory = db
    problem = _seed(session)
    response = client.delete(_url(problem, "no-such-block"))
    assert response.status_code == 404
    assert response.json() == {"detail": "Reserved activity not found"}


def test_delete_locked_returns_409(client, db):
    session, _session_factory = db
    problem = _seed(session)
    generate_response = client.post(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/generate"
    )
    assert generate_response.status_code == 201

    response = client.delete(_url(problem, "club_chess"))
    assert response.status_code == 409
    assert response.json()["code"] == "SCHEDULING_CONFIGURATION_LOCKED"


def test_delete_reflected_in_config(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.delete(_url(problem, "club_chess"))
    assert response.status_code == 200

    config_body = client.get(f"/schools/{problem.school.id}/years/{problem.academic_year.id}/config").json()
    assert all(b["id"] != "club_chess" for b in config_body["reserved_blocks"])


# -- CONFIGURATION LOCK PROJECTION -------------------------------------------

def test_get_configuration_locked_true_after_generate_and_data_still_readable(client, db):
    session, _session_factory = db
    problem = _seed(session)
    generate_response = client.post(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/generate"
    )
    assert generate_response.status_code == 201

    response = client.get(_url(problem))
    assert response.status_code == 200
    body = response.json()
    assert body["configuration_locked"] is True
    assert len(body["reserved_activities"]) == 2
