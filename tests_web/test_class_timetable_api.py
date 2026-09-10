"""Real-PostgreSQL, real-solver/verifier HTTP integration tests for
Phase 3B.1's `GET .../schedule/active/classes/{class_section_id}`
(`docs/DECISIONS.md` #32's locked HTTP contract).

Uses the same `join_transaction_mode="create_savepoint"` fixture
pattern as `tests_web/test_schedule_api.py`: every adapter-opened
`Session` shares the fixture's single outer, never-committed
`Connection`, so nothing here ever leaves a row in the real database.

Split/merged/reserved-block slots are never hard-coded here -- they are
discovered from the actual solved/persisted schedule at test time, so
these tests remain valid against any equally-correct solver placement
(see `test_real_split_group_projection_contains_both_branches` for why).
"""
from __future__ import annotations

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from school_timetable.api.dependencies import (
    get_class_timetable_service,
    get_generate_schedule_service,
    get_schedule_version_repository,
)
from school_timetable.api.main import app
from school_timetable.application.class_timetable_service import ClassTimetableService
from school_timetable.application.generate_schedule_service import GenerateScheduleService
from school_timetable.fixtures.valid_fixture import build_valid_fixture
from school_timetable.persistence import models as m
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


def _client(session_factory, *, raise_server_exceptions: bool = True) -> TestClient:
    def override_schedule_repo():
        return SqlAlchemyScheduleVersionRepository(session_factory)

    def override_generate_service():
        return GenerateScheduleService(
            SessionFactorySchedulingProblemRepository(session_factory),
            SqlAlchemyScheduleVersionRepository(session_factory),
        )

    def override_class_timetable_service():
        return ClassTimetableService(
            SessionFactorySchedulingProblemRepository(session_factory),
            SqlAlchemyScheduleVersionRepository(session_factory),
        )

    app.dependency_overrides[get_schedule_version_repository] = override_schedule_repo
    app.dependency_overrides[get_generate_schedule_service] = override_generate_service
    app.dependency_overrides[get_class_timetable_service] = override_class_timetable_service
    return TestClient(app, raise_server_exceptions=raise_server_exceptions)


@pytest.fixture
def client(db):
    _session, session_factory = db
    try:
        yield _client(session_factory)
    finally:
        app.dependency_overrides.pop(get_schedule_version_repository, None)
        app.dependency_overrides.pop(get_generate_schedule_service, None)
        app.dependency_overrides.pop(get_class_timetable_service, None)


def _seed_and_generate(client, session, session_factory):
    problem = build_valid_fixture()
    write_scheduling_problem(session, problem)
    session.flush()

    generate_response = client.post(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/generate"
    )
    assert generate_response.status_code == 201

    active = SqlAlchemyScheduleVersionRepository(session_factory).get_active_schedule(
        problem.school.id, problem.academic_year.id
    )
    assert active is not None
    return problem, active


def _cell(body: dict, day_id: str, period_id: str) -> dict:
    row = next(r for r in body["rows"] if r["period_id"] == period_id)
    return next(c for c in row["cells"] if c["day_id"] == day_id)


def test_real_split_group_projection_contains_both_branches(client, db):
    session, session_factory = db
    problem, active = _seed_and_generate(client, session, session_factory)

    german_slots = {(e.day_id, e.period_id) for e in active.entries if e.requirement_id == "german_8a"}
    russian_slots = {(e.day_id, e.period_id) for e in active.entries if e.requirement_id == "russian_8a"}
    # Domain invariant under test: the two split branches are always
    # synchronized to the identical slot set (never hard-coded here --
    # discovered from this actual solve).
    assert german_slots == russian_slots
    assert german_slots

    response = client.get(f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/active/classes/8a")
    assert response.status_code == 200
    body = response.json()

    for day_id, period_id in german_slots:
        cell = _cell(body, day_id, period_id)
        by_requirement = {e["requirement_id"]: e for e in cell["entries"]}
        assert "german_8a" in by_requirement
        assert "russian_8a" in by_requirement
        german_entry = by_requirement["german_8a"]
        russian_entry = by_requirement["russian_8a"]
        assert german_entry["activity_name"] == "German"
        assert german_entry["teacher_name"] == "Teacher German"
        assert german_entry["participant_group_name"] == "8-A German"
        assert russian_entry["activity_name"] == "Russian"
        assert russian_entry["teacher_name"] == "Teacher Russian"
        assert russian_entry["participant_group_name"] == "8-A Russian"


def test_real_merged_group_projection_appears_once_per_class(client, db):
    session, session_factory = db
    problem, active = _seed_and_generate(client, session, session_factory)

    merged_entries = [e for e in active.entries if e.requirement_id == "history_merged_9a_9b"]
    assert merged_entries
    slots = {(e.day_id, e.period_id) for e in merged_entries}

    response_9a = client.get(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/active/classes/9a"
    )
    response_9b = client.get(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/active/classes/9b"
    )
    assert response_9a.status_code == 200
    assert response_9b.status_code == 200
    body_9a = response_9a.json()
    body_9b = response_9b.json()

    for day_id, period_id in slots:
        cell_9a = _cell(body_9a, day_id, period_id)
        cell_9b = _cell(body_9b, day_id, period_id)
        merged_in_9a = [e for e in cell_9a["entries"] if e["requirement_id"] == "history_merged_9a_9b"]
        merged_in_9b = [e for e in cell_9b["entries"] if e["requirement_id"] == "history_merged_9a_9b"]
        assert len(merged_in_9a) == 1
        assert len(merged_in_9b) == 1


def test_real_reserved_block_projection_in_every_configured_class(client, db):
    session, session_factory = db
    problem, active = _seed_and_generate(client, session, session_factory)

    chess_entries = [e for e in active.entries if e.reserved_block_id == "club_chess"]
    assert chess_entries
    day_id, period_id = chess_entries[0].day_id, chess_entries[0].period_id

    for class_id in ("8a", "8b"):
        response = client.get(
            f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/active/classes/{class_id}"
        )
        assert response.status_code == 200
        cell = _cell(response.json(), day_id, period_id)
        chess = next(e for e in cell["entries"] if e["reserved_block_id"] == "club_chess")
        assert chess["source"] == "RESERVED_BLOCK"
        assert chess["activity_name"] == "Chess Club"
        assert chess["teacher_id"] is None
        assert chess["teacher_name"] is None
        assert chess["participant_group_id"] is None
        assert chess["participant_group_name"] is None


def test_real_reserved_block_resource_visible_in_class_timetable(client, db):
    """Resources B2: a `ReservedBlock` carrying a fixed Resource must
    expose it through the exact same `resource_id` field an ordinary
    lesson entry already uses -- resolvable identically, never a
    separate mechanism. `build_valid_fixture()`'s own reserved blocks
    never carry a Resource by default, so this test injects one onto
    `club_chess` before solving."""
    session, session_factory = db
    problem = build_valid_fixture()
    problem = replace(
        problem,
        reserved_blocks=tuple(
            replace(b, resource_id="gym") if b.id == "club_chess" else b for b in problem.reserved_blocks
        ),
    )
    write_scheduling_problem(session, problem)
    session.flush()

    generate_response = client.post(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/generate"
    )
    assert generate_response.status_code == 201

    active = SqlAlchemyScheduleVersionRepository(session_factory).get_active_schedule(
        problem.school.id, problem.academic_year.id
    )
    chess_entries = [e for e in active.entries if e.reserved_block_id == "club_chess"]
    assert chess_entries
    assert chess_entries[0].resource_id == "gym"
    day_id, period_id = chess_entries[0].day_id, chess_entries[0].period_id

    response = client.get(f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/active/classes/8a")
    assert response.status_code == 200
    cell = _cell(response.json(), day_id, period_id)
    chess = next(e for e in cell["entries"] if e["reserved_block_id"] == "club_chess")
    assert chess["resource_id"] == "gym"


def test_real_projection_response_shape_and_order(client, db):
    session, session_factory = db
    problem, active = _seed_and_generate(client, session, session_factory)

    response = client.get(f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/active/classes/8a")
    assert response.status_code == 200
    body = response.json()

    assert set(body.keys()) == {
        "school_id", "school_name", "academic_year_id", "academic_year_label",
        "class_section_id", "class_section_name", "version_number", "solver_status",
        "total_soft_penalty", "created_at", "is_active", "days", "rows",
    }
    assert body["is_active"] is True
    assert body["class_section_id"] == "8a"
    assert "wall_time_seconds" not in body
    assert "random_seed" not in body

    days = sorted(problem.days, key=lambda d: d.index)
    instructional_periods = sorted((p for p in problem.periods if p.is_instructional), key=lambda p: p.index)
    assert [d["id"] for d in body["days"]] == [d.id for d in days]
    assert set(body.get("days")[0].keys()) == {"id", "name"}
    assert [row["period_id"] for row in body["rows"]] == [p.id for p in instructional_periods]

    for row in body["rows"]:
        assert set(row.keys()) == {"period_id", "period_name", "cells"}
        assert [c["day_id"] for c in row["cells"]] == [d.id for d in days]
        assert len(row["cells"]) == len(days)
        for cell in row["cells"]:
            assert set(cell.keys()) == {"day_id", "entries"}
            for entry in cell["entries"]:
                assert set(entry.keys()) == {
                    "source", "activity_id", "activity_name", "teacher_id", "teacher_name",
                    "participant_group_id", "participant_group_name", "requirement_id",
                    "reserved_block_id", "resource_id",
                }
                assert "ordinal" not in entry
                assert entry["source"] in ("REQUIREMENT", "RESERVED_BLOCK")


def test_unknown_school_year_returns_config_not_found(client):
    response = client.get("/schools/no-such-school/years/no-such-year/schedule/active/classes/8a")
    assert response.status_code == 404
    assert response.json() == {"detail": "Scheduling configuration not found"}


def test_valid_config_no_schedule_returns_active_schedule_not_found(db):
    session, session_factory = db
    problem = build_valid_fixture()
    write_scheduling_problem(session, problem)
    session.flush()

    client = _client(session_factory)
    try:
        response = client.get(
            f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/active/classes/8a"
        )
        assert response.status_code == 404
        assert response.json() == {"detail": "Active schedule not found"}
    finally:
        app.dependency_overrides.pop(get_schedule_version_repository, None)
        app.dependency_overrides.pop(get_generate_schedule_service, None)
        app.dependency_overrides.pop(get_class_timetable_service, None)


def test_unknown_class_section_returns_class_section_not_found(client, db):
    session, session_factory = db
    problem, _active = _seed_and_generate(client, session, session_factory)

    response = client.get(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/active/classes/no-such-class"
    )
    assert response.status_code == 404
    assert response.json() == {"detail": "Class section not found"}


def test_corrupt_active_schedule_state_does_not_leak_internal_detail(db):
    session, session_factory = db
    problem = build_valid_fixture()
    write_scheduling_problem(session, problem)
    session.flush()
    year_id = session.execute(
        select(m.AcademicYear.id).where(m.AcademicYear.natural_id == problem.academic_year.id)
    ).scalar_one()

    # Test-only corruption: insert a bare `Schedule` row directly via the
    # ORM, bypassing generation entirely, so `active_version_id` stays
    # NULL outside any atomic creation window.
    session.add(m.Schedule(academic_year_id=year_id, active_version_id=None))
    session.flush()

    client = _client(session_factory, raise_server_exceptions=False)
    try:
        response = client.get(
            f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/active/classes/8a"
        )
        assert response.status_code == 500
        assert "active_version_id" not in response.text
        assert "CorruptScheduleStateError" not in response.text
    finally:
        app.dependency_overrides.pop(get_schedule_version_repository, None)
        app.dependency_overrides.pop(get_generate_schedule_service, None)
        app.dependency_overrides.pop(get_class_timetable_service, None)
