"""Real-PostgreSQL integration tests for
`GET /schools/{school_id}/years/{year_id}/config` (Phase 3A2.4).

Uses the same rollback-based `db_session` fixture pattern already
established in `test_problem_repository.py`, and overrides the FastAPI
`get_session` dependency so the route's repository sees the SAME
Session/transaction that `write_scheduling_problem()` used to seed
data -- never a separate, independently-committed session. Nothing here
is ever committed; the development database is never touched.
"""
from __future__ import annotations

import dataclasses

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from school_timetable.api.main import app
from school_timetable.api.serializer import config_response_from_problem
from school_timetable.fixtures.valid_fixture import build_valid_fixture
from school_timetable.persistence.db import get_session
from tests_web.support.problem_writer import write_scheduling_problem


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


@pytest.fixture
def client(db_session):
    def override_get_session():
        yield db_session

    app.dependency_overrides[get_session] = override_get_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_session, None)


def test_config_endpoint_returns_full_response_matching_original_problem(client, db_session):
    problem = build_valid_fixture()
    write_scheduling_problem(db_session, problem)
    db_session.flush()

    response = client.get(f"/schools/{problem.school.id}/years/{problem.academic_year.id}/config")
    assert response.status_code == 200

    # Strong contract proof: the whole response equals the whole
    # expected response built by the same serializer against the
    # original in-memory problem -- not a scattered subset of fields.
    expected = config_response_from_problem(problem)
    assert response.json() == expected.model_dump(mode="json")


def test_config_endpoint_representative_ordering_and_natural_ids(client, db_session):
    problem = build_valid_fixture()
    write_scheduling_problem(db_session, problem)
    db_session.flush()

    response = client.get(f"/schools/{problem.school.id}/years/{problem.academic_year.id}/config")
    body = response.json()

    merged_group = next(g for g in body["participant_groups"] if g["id"] == "pg_9a_9b_merged")
    assert merged_group["class_sections"] == ["9a", "9b"]

    math_8a = next(r for r in body["teaching_requirements"] if r["id"] == "math_8a")
    assert math_8a["block_policy"] == {"mode": "REQUIRED", "block_sizes": [2, 1, 1, 1]}
    assert math_8a["teacher_id"] == "t_math"
    assert math_8a["activity_id"] == "math"
    assert math_8a["participant_group_id"] == "pg_8a"
    assert math_8a["weekly_periods"] == 5

    history_8a = next(r for r in body["teaching_requirements"] if r["id"] == "history_8a")
    assert history_8a["time_preferences"][0]["preferred_periods"] == [0, 1]

    sport_8a = next(r for r in body["teaching_requirements"] if r["id"] == "sport_8a")
    assert sport_8a["resource_requirement"] == {"resource_id": "gym"}

    chess_block = next(b for b in body["reserved_blocks"] if b["id"] == "club_chess")
    assert chess_block["class_sections"] == ["8a", "8b"]
    assert chess_block["slots"] == [{"day_id": "wed", "period_id": "p8"}]
    assert chess_block["teacher_id"] is None

    fixed = next(fp for fp in body["fixed_placements"] if fp["id"] == "fixed_art_8b")
    assert fixed["requirement_id"] == "art_8b"
    assert fixed["slot"] == {"day_id": "mon", "period_id": "p1"}


def test_config_endpoint_no_surrogate_id_leaks(client, db_session):
    problem = build_valid_fixture()
    write_scheduling_problem(db_session, problem)
    db_session.flush()

    response = client.get(f"/schools/{problem.school.id}/years/{problem.academic_year.id}/config")
    body = response.json()

    def walk(node):
        if isinstance(node, dict):
            for key, value in node.items():
                # Persistence-only concepts must never appear as keys.
                assert key not in ("academic_year_id", "ordinal"), f"leaked persistence key: {key!r}"
                # Every public "id"/"*_id" value is a natural string --
                # a surrogate BIGINT would serialize as a JSON number.
                if (key == "id" or key.endswith("_id")) and value is not None:
                    assert isinstance(value, str), f"{key} leaked a non-string value: {value!r}"
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(body)


def test_config_endpoint_unknown_school_and_unknown_year_both_404(client, db_session):
    problem = build_valid_fixture()
    write_scheduling_problem(db_session, problem)
    db_session.flush()

    unknown_school = client.get(f"/schools/no-such-school/years/{problem.academic_year.id}/config")
    unknown_year = client.get(f"/schools/{problem.school.id}/years/no-such-year/config")

    assert unknown_school.status_code == 404
    assert unknown_year.status_code == 404
    # Same public shape either way -- the caller cannot distinguish
    # "school missing" from "year missing" from the response alone.
    assert unknown_school.json() == unknown_year.json() == {"detail": "Scheduling configuration not found"}


def test_config_endpoint_scope_isolation_with_overlapping_natural_ids(client, db_session):
    """Two snapshots share every natural ID except school/academic-year
    -- one representative HTTP-level proof that the route (via the
    Phase 3A2.3 repository underneath it) never mixes rows from the
    other snapshot into the response."""
    base_a, base_b = build_valid_fixture(), build_valid_fixture()
    problem_a = dataclasses.replace(
        base_a,
        school=dataclasses.replace(base_a.school, id="school-a"),
        academic_year=dataclasses.replace(base_a.academic_year, id="year-a"),
    )
    problem_b = dataclasses.replace(
        base_b,
        school=dataclasses.replace(base_b.school, id="school-b"),
        academic_year=dataclasses.replace(base_b.academic_year, id="year-b"),
    )
    write_scheduling_problem(db_session, problem_a)
    write_scheduling_problem(db_session, problem_b)
    db_session.flush()

    response_a = client.get("/schools/school-a/years/year-a/config")
    response_b = client.get("/schools/school-b/years/year-b/config")

    assert response_a.json() == config_response_from_problem(problem_a).model_dump(mode="json")
    assert response_b.json() == config_response_from_problem(problem_b).model_dump(mode="json")
