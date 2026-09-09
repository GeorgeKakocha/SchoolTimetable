"""Real-PostgreSQL, real-solver/verifier HTTP integration tests for the
teacher-timetable sibling projection,
`GET .../schedule/active/teachers/{teacher_id}` (next product slice
after Phase 3C.3, no new phase number). Mirrors
`tests_web/test_class_timetable_api.py` exactly.

Uses the same `join_transaction_mode="create_savepoint"` fixture
pattern: every adapter-opened `Session` shares the fixture's single
outer, never-committed `Connection`, so nothing here ever leaves a row
in the real database.

Split/merged-group slots are never hard-coded here -- they are
discovered from the actual solved/persisted schedule at test time, so
these tests remain valid against any equally-correct solver placement.
"""
from __future__ import annotations

import dataclasses

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from school_timetable.api.dependencies import (
    get_generate_schedule_service,
    get_schedule_version_repository,
    get_teacher_timetable_service,
)
from school_timetable.api.main import app
from school_timetable.application.generate_schedule_service import GenerateScheduleService
from school_timetable.application.teacher_timetable_service import TeacherTimetableService
from school_timetable.domain.people import Teacher
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

    def override_teacher_timetable_service():
        return TeacherTimetableService(
            SessionFactorySchedulingProblemRepository(session_factory),
            SqlAlchemyScheduleVersionRepository(session_factory),
        )

    app.dependency_overrides[get_schedule_version_repository] = override_schedule_repo
    app.dependency_overrides[get_generate_schedule_service] = override_generate_service
    app.dependency_overrides[get_teacher_timetable_service] = override_teacher_timetable_service
    return TestClient(app, raise_server_exceptions=raise_server_exceptions)


@pytest.fixture
def client(db):
    _session, session_factory = db
    try:
        yield _client(session_factory)
    finally:
        app.dependency_overrides.pop(get_schedule_version_repository, None)
        app.dependency_overrides.pop(get_generate_schedule_service, None)
        app.dependency_overrides.pop(get_teacher_timetable_service, None)


def _seed_and_generate(client, session, session_factory, problem=None):
    problem = problem if problem is not None else build_valid_fixture()
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


def test_real_whole_class_entry_projection(client, db):
    session, session_factory = db
    problem, active = _seed_and_generate(client, session, session_factory)

    math_entries = [e for e in active.entries if e.requirement_id == "math_8a"]
    assert math_entries

    response = client.get(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/active/teachers/t_math"
    )
    assert response.status_code == 200
    body = response.json()
    assert body["teacher_id"] == "t_math"
    assert body["teacher_name"] == "Teacher Math"

    entry = math_entries[0]
    cell = _cell(body, entry.day_id, entry.period_id)
    by_requirement = {e["requirement_id"]: e for e in cell["entries"]}
    assert "math_8a" in by_requirement
    math = by_requirement["math_8a"]
    assert math["activity_name"] == "Mathematics"
    assert math["participant_group_name"] == "All of 8-A"
    assert math["participant_group_role"] == "WHOLE_CLASS"
    assert math["class_sections"] == [{"id": "8a", "name": "8-A"}]


def test_real_subgroup_entry_projection(client, db):
    session, session_factory = db
    problem, active = _seed_and_generate(client, session, session_factory)

    german_entries = [e for e in active.entries if e.requirement_id == "german_8a"]
    assert german_entries

    response = client.get(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/active/teachers/t_german"
    )
    assert response.status_code == 200
    body = response.json()

    entry = german_entries[0]
    cell = _cell(body, entry.day_id, entry.period_id)
    german = next(e for e in cell["entries"] if e["requirement_id"] == "german_8a")
    assert german["activity_name"] == "German"
    assert german["participant_group_name"] == "8-A German"
    assert german["participant_group_role"] == "SUBGROUP"
    assert german["class_sections"] == [{"id": "8a", "name": "8-A"}]


def test_real_merged_classes_entry_projection(client, db):
    session, session_factory = db
    problem, active = _seed_and_generate(client, session, session_factory)

    merged_entries = [e for e in active.entries if e.requirement_id == "history_merged_9a_9b"]
    assert merged_entries

    response = client.get(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/active/teachers/t_history"
    )
    assert response.status_code == 200
    body = response.json()

    entry = merged_entries[0]
    cell = _cell(body, entry.day_id, entry.period_id)
    merged = next(e for e in cell["entries"] if e["requirement_id"] == "history_merged_9a_9b")
    assert merged["activity_name"] == "History"
    assert merged["participant_group_name"] == "9-A + 9-B Merged History"
    assert merged["participant_group_role"] == "MERGED_CLASSES"
    assert {c["id"] for c in merged["class_sections"]} == {"9a", "9b"}


def test_real_only_requested_teacher_entries_appear_no_leak_across_teachers(client, db):
    session, session_factory = db
    problem, active = _seed_and_generate(client, session, session_factory)

    math_response = client.get(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/active/teachers/t_math"
    )
    assert math_response.status_code == 200
    all_entries = [
        e for row in math_response.json()["rows"] for cell in row["cells"] for e in cell["entries"]
    ]
    # Every entry in Teacher Math's own projection must be one of the
    # requirements actually assigned to t_math in the real solved
    # schedule -- never an entry belonging to another teacher.
    math_requirement_ids = {e.requirement_id for e in active.entries if e.teacher_id == "t_math"}
    assert all_entries
    assert {e["requirement_id"] for e in all_entries} <= math_requirement_ids
    assert {e["requirement_id"] for e in all_entries} == math_requirement_ids


def test_real_free_periods_are_empty_cells(client, db):
    session, session_factory = db
    problem, active = _seed_and_generate(client, session, session_factory)

    response = client.get(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/active/teachers/t_math"
    )
    assert response.status_code == 200
    body = response.json()

    occupied = {(e.day_id, e.period_id) for e in active.entries if e.teacher_id == "t_math"}
    all_slots = {(cell["day_id"], row["period_id"]) for row in body["rows"] for cell in row["cells"]}
    free_slots = all_slots - occupied
    assert free_slots
    for day_id, period_id in free_slots:
        cell = _cell(body, day_id, period_id)
        assert cell["entries"] == []


def test_real_zero_load_teacher_returns_valid_all_empty_grid(client, db):
    session, session_factory = db
    problem = build_valid_fixture()
    problem = dataclasses.replace(
        problem, teachers=problem.teachers + (Teacher(id="t_idle", first_name="Teacher Idle", last_name=""),),
    )
    _problem, active = _seed_and_generate(client, session, session_factory, problem=problem)

    assert not any(e.teacher_id == "t_idle" for e in active.entries)

    response = client.get(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/active/teachers/t_idle"
    )
    assert response.status_code == 200
    body = response.json()
    assert body["teacher_id"] == "t_idle"
    assert body["teacher_name"] == "Teacher Idle"
    all_entries = [e for row in body["rows"] for cell in row["cells"] for e in cell["entries"]]
    assert all_entries == []
    assert len(body["rows"]) > 0


def test_real_projection_response_shape_and_order(client, db):
    session, session_factory = db
    problem, active = _seed_and_generate(client, session, session_factory)

    response = client.get(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/active/teachers/t_math"
    )
    assert response.status_code == 200
    body = response.json()

    assert set(body.keys()) == {
        "school_id", "school_name", "academic_year_id", "academic_year_label",
        "teacher_id", "teacher_name", "version_number", "solver_status",
        "total_soft_penalty", "created_at", "is_active", "days", "rows",
    }
    assert body["is_active"] is True
    assert body["teacher_id"] == "t_math"
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
                    "source", "activity_id", "activity_name", "participant_group_id",
                    "participant_group_name", "participant_group_role", "class_sections",
                    "requirement_id", "reserved_block_id", "resource_id",
                }
                assert "ordinal" not in entry
                assert "teacher_id" not in entry
                assert "teacher_name" not in entry
                assert entry["source"] in ("REQUIREMENT", "RESERVED_BLOCK")


def test_unknown_school_year_returns_config_not_found(client):
    response = client.get("/schools/no-such-school/years/no-such-year/schedule/active/teachers/t_math")
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
            f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/active/teachers/t_math"
        )
        assert response.status_code == 404
        assert response.json() == {"detail": "Active schedule not found"}
    finally:
        app.dependency_overrides.pop(get_schedule_version_repository, None)
        app.dependency_overrides.pop(get_generate_schedule_service, None)
        app.dependency_overrides.pop(get_teacher_timetable_service, None)


def test_unknown_teacher_returns_teacher_not_found(client, db):
    session, session_factory = db
    problem, _active = _seed_and_generate(client, session, session_factory)

    response = client.get(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/active/teachers/no-such-teacher"
    )
    assert response.status_code == 404
    assert response.json() == {"detail": "Teacher not found"}


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
            f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/active/teachers/t_math"
        )
        assert response.status_code == 500
        assert "active_version_id" not in response.text
        assert "CorruptScheduleStateError" not in response.text
    finally:
        app.dependency_overrides.pop(get_schedule_version_repository, None)
        app.dependency_overrides.pop(get_generate_schedule_service, None)
        app.dependency_overrides.pop(get_teacher_timetable_service, None)
