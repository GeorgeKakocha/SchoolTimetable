"""PostgreSQL-backed HTTP integration tests for the Teacher Matrix."""
from __future__ import annotations

import dataclasses

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from school_timetable.api.dependencies import (
    get_generate_schedule_service,
    get_schedule_version_repository,
    get_teacher_timetable_matrix_service,
)
from school_timetable.api.main import app
from school_timetable.application.generate_schedule_service import GenerateScheduleService
from school_timetable.application.teacher_timetable_matrix_service import (
    TeacherTimetableMatrixService,
)
from school_timetable.domain.calendar import Period
from school_timetable.domain.people import Teacher
from school_timetable.domain.schedule import Schedule
from school_timetable.fixtures.valid_fixture import build_valid_fixture
from school_timetable.persistence import models as m
from school_timetable.persistence.configuration_revision_repository import (
    SqlAlchemyConfigurationRevisionRepository,
)
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
    def matrix_service():
        return TeacherTimetableMatrixService(
            SessionFactorySchedulingProblemRepository(session_factory),
            SqlAlchemyScheduleVersionRepository(session_factory),
        )

    def generate_service():
        return GenerateScheduleService(
            SessionFactorySchedulingProblemRepository(session_factory),
            SqlAlchemyScheduleVersionRepository(session_factory),
        )

    def schedule_repository():
        return SqlAlchemyScheduleVersionRepository(session_factory)

    app.dependency_overrides[get_teacher_timetable_matrix_service] = matrix_service
    app.dependency_overrides[get_generate_schedule_service] = generate_service
    app.dependency_overrides[get_schedule_version_repository] = schedule_repository
    return TestClient(app)


@pytest.fixture
def client(db):
    _session, session_factory = db
    try:
        yield _client(session_factory)
    finally:
        app.dependency_overrides.pop(get_teacher_timetable_matrix_service, None)
        app.dependency_overrides.pop(get_generate_schedule_service, None)
        app.dependency_overrides.pop(get_schedule_version_repository, None)


def _matrix_url(problem, version_number: int | None = None) -> str:
    root = f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule"
    if version_number is None:
        return f"{root}/active/teacher-matrix"
    return f"{root}/versions/{version_number}/teacher-matrix"


def _seed_and_generate(client, session, session_factory):
    problem = build_valid_fixture()
    problem = dataclasses.replace(
        problem,
        teachers=(Teacher("t_idle", "Teacher", "Idle"),) + problem.teachers,
        periods=problem.periods + (
            Period(
                "assembly", "Assembly", len(problem.periods), "structural",
                is_instructional=False,
            ),
        ),
        reserved_blocks=tuple(
            dataclasses.replace(block, teacher_id="t_art", resource_id="gym")
            if block.id == "club_chess" else block
            for block in problem.reserved_blocks
        ),
    )
    write_scheduling_problem(session, problem)
    session.flush()
    response = client.post(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/generate"
    )
    assert response.status_code == 201, response.text
    active = SqlAlchemyScheduleVersionRepository(session_factory).get_active_schedule(
        problem.school.id, problem.academic_year.id,
    )
    assert active is not None
    return problem, active


def _teacher(body: dict, teacher_id: str) -> dict:
    return next(teacher for teacher in body["teachers"] if teacher["id"] == teacher_id)


def _entry(body: dict, *, requirement_id: str | None = None, reserved_block_id: str | None = None):
    return next(
        entry
        for teacher in body["teachers"]
        for cell in teacher["cells"]
        for entry in cell["entries"]
        if entry["requirement_id"] == requirement_id
        and entry["reserved_block_id"] == reserved_block_id
    )


def test_active_matrix_contract_ordering_sparse_cells_and_real_entry_semantics(client, db):
    session, session_factory = db
    problem, active = _seed_and_generate(client, session, session_factory)

    response = client.get(_matrix_url(problem))
    assert response.status_code == 200
    body = response.json()

    assert body["version_number"] == active.version_number
    assert body["solver_status"] == active.solver_status.value
    assert body["total_soft_penalty"] == active.total_soft_penalty
    assert body["is_active"] is True
    assert [teacher["id"] for teacher in body["teachers"]] == [t.id for t in problem.teachers]
    assert _teacher(body, "t_idle") == {"id": "t_idle", "name": "Teacher Idle", "cells": []}
    assert [day["id"] for day in body["days"]] == [
        day.id for day in sorted(problem.days, key=lambda item: item.index)
    ]
    assert [period["id"] for period in body["periods"]] == [
        period.id
        for period in sorted(problem.periods, key=lambda item: item.index)
        if period.is_instructional
    ]
    assert "assembly" not in {period["id"] for period in body["periods"]}

    math = _entry(body, requirement_id="math_8a", reserved_block_id=None)
    assert math["activity_name"] == "Mathematics"
    assert math["participant_group_name"] == "All of 8-A"
    assert math["participant_group_role"] == "WHOLE_CLASS"
    assert math["class_sections"] == [{"id": "8a", "name": "8-A"}]

    german = _entry(body, requirement_id="german_8a", reserved_block_id=None)
    assert german["participant_group_name"] == "8-A German"
    assert german["participant_group_role"] == "SUBGROUP"

    merged = _entry(body, requirement_id="history_merged_9a_9b", reserved_block_id=None)
    assert merged["participant_group_name"] == "9-A + 9-B Merged History"
    assert merged["participant_group_role"] == "MERGED_CLASSES"
    assert {tuple(section.items()) for section in merged["class_sections"]} == {
        (("id", "9a"), ("name", "9-A")),
        (("id", "9b"), ("name", "9-B")),
    }

    chess = _entry(body, requirement_id=None, reserved_block_id="club_chess")
    assert chess["source"] == "RESERVED_BLOCK"
    assert chess["resource_id"] == "gym"
    assert any(
        entry["reserved_block_id"] == "club_chess"
        for cell in _teacher(body, "t_art")["cells"]
        for entry in cell["entries"]
    )

    occupied_by_teacher = {
        teacher_id: {(entry.day_id, entry.period_id) for entry in active.entries if entry.teacher_id == teacher_id}
        for teacher_id in (teacher["id"] for teacher in body["teachers"])
    }
    for teacher in body["teachers"]:
        serialized = {(cell["day_id"], cell["period_id"]) for cell in teacher["cells"]}
        assert serialized == occupied_by_teacher[teacher["id"]]
        assert all(cell["entries"] for cell in teacher["cells"])


def test_contract_contains_only_public_fields_and_no_materialized_free_coordinates(client, db):
    session, session_factory = db
    problem, _active = _seed_and_generate(client, session, session_factory)
    body = client.get(_matrix_url(problem)).json()

    assert set(body) == {
        "school_id", "school_name", "academic_year_id", "academic_year_label",
        "version_number", "solver_status", "total_soft_penalty", "created_at",
        "is_active", "days", "periods", "teachers",
    }
    forbidden = {"configuration_revision_id", "schedule_version_id", "ordinal", "surrogate_id"}

    def inspect(value):
        if isinstance(value, dict):
            assert forbidden.isdisjoint(value)
            for nested in value.values():
                inspect(nested)
        elif isinstance(value, list):
            for nested in value:
                inspect(nested)

    inspect(body)
    expected_cell_keys = {"day_id", "period_id", "entries"}
    assert all(
        set(cell) == expected_cell_keys and cell["entries"]
        for teacher in body["teachers"]
        for cell in teacher["cells"]
    )


def test_active_and_historical_routes_ignore_open_draft_and_version_promotion_is_stable(client, db):
    session, session_factory = db
    problem, active_v1 = _seed_and_generate(client, session, session_factory)
    repo = SqlAlchemyScheduleVersionRepository(session_factory)

    SqlAlchemyConfigurationRevisionRepository(session_factory).begin_draft(
        problem.school.id, problem.academic_year.id,
    )
    year = session.execute(
        select(m.AcademicYear).join(m.School).where(
            m.School.natural_id == problem.school.id,
            m.AcademicYear.natural_id == problem.academic_year.id,
        )
    ).scalar_one()
    assert year.draft_revision_id is not None
    draft_math = session.execute(
        select(m.Activity).where(
            m.Activity.academic_year_id == year.id,
            m.Activity.configuration_revision_id == year.draft_revision_id,
            m.Activity.natural_id == "math",
        )
    ).scalar_one()
    draft_math.name = "Draft-only Mathematics Name"
    session.flush()

    active_before = client.get(_matrix_url(problem))
    historical_before = client.get(_matrix_url(problem, 1))
    assert active_before.status_code == historical_before.status_code == 200
    assert _entry(active_before.json(), requirement_id="math_8a", reserved_block_id=None)["activity_name"] == "Mathematics"
    assert _entry(historical_before.json(), requirement_id="math_8a", reserved_block_id=None)["activity_name"] == "Mathematics"

    active_v2 = repo.persist_edited_version(
        problem.school.id,
        problem.academic_year.id,
        active_v1.version_number,
        Schedule(active_v1.entries, active_v1.locked_occurrences),
        active_v1.solver_status,
        active_v1.total_soft_penalty,
        active_v1.wall_time_seconds,
        active_v1.random_seed,
    )
    assert active_v2.version_number == 2

    active_after = client.get(_matrix_url(problem))
    historical_after = client.get(_matrix_url(problem, 1))
    requested_v2 = client.get(_matrix_url(problem, 2))
    assert active_after.status_code == historical_after.status_code == requested_v2.status_code == 200
    assert active_after.json()["version_number"] == 2
    assert active_after.json()["is_active"] is True
    assert requested_v2.json()["is_active"] is True
    assert historical_after.json()["version_number"] == 1
    assert historical_after.json()["is_active"] is False
    assert historical_after.json() == {**historical_before.json(), "is_active": False}


def test_no_active_schedule_and_unknown_school_use_established_404_bodies(client, db):
    session, _session_factory = db
    problem = build_valid_fixture()
    write_scheduling_problem(session, problem)
    session.flush()

    no_schedule = client.get(_matrix_url(problem))
    assert no_schedule.status_code == 404
    assert no_schedule.json() == {"detail": "Active schedule not found"}

    unknown = client.get("/schools/missing/years/missing/schedule/active/teacher-matrix")
    assert unknown.status_code == 404
    assert unknown.json() == {"detail": "Scheduling configuration not found"}


def test_missing_historical_version_uses_established_structured_404(client, db):
    session, session_factory = db
    problem, _active = _seed_and_generate(client, session, session_factory)

    response = client.get(_matrix_url(problem, 999))
    assert response.status_code == 404
    assert response.json()["code"] == "SCHEDULE_VERSION_NOT_FOUND"
    assert response.json()["version_number"] == 999
