"""Real-PostgreSQL, real-solver/verifier HTTP integration tests for
Phase 3A3.4's `GET .../schedule/active` and `POST .../schedule/generate`
(`docs/DECISIONS.md` #31's locked HTTP contract, fully closed by the
Phase 3A3.4 pre-implementation contract-lock).

Uses the same `join_transaction_mode="create_savepoint"` fixture
pattern as `tests_web/test_schedule_repository.py`/
`tests_web/test_generate_schedule_service_integration.py`: every
adapter-opened `Session` (via the overridden
`get_schedule_version_repository`/`get_generate_schedule_service`
dependencies) shares the fixture's single outer, never-committed
`Connection`, so nothing here ever leaves a row in the real database.
`get_session`/`get_scheduling_problem_repository` (the `/config`
dependency) is deliberately NOT overridden here -- the schedule routes
never use it.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from school_timetable.api.dependencies import get_generate_schedule_service, get_schedule_version_repository
from school_timetable.api.main import app
from school_timetable.api.serializer import schedule_entry_response_from_entry
from school_timetable.application import generate_schedule_service as svc_mod
from school_timetable.application.generate_schedule_service import GenerateScheduleService
from school_timetable.domain.problem import SchedulingProblem
from school_timetable.domain.result import SchedulingResult, SolverStatus
from school_timetable.fixtures.impossible_fixture import build_impossible_fixture
from school_timetable.fixtures.school_scale.scenarios import build_school_scale_impossible
from school_timetable.fixtures.valid_fixture import build_valid_fixture
from school_timetable.persistence import models as m
from school_timetable.persistence.problem_repository import SessionFactorySchedulingProblemRepository
from school_timetable.persistence.schedule_repository import SqlAlchemyScheduleVersionRepository
from school_timetable.validation.preflight import run_preflight
from school_timetable.verification.verifier import VerificationReport
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

    app.dependency_overrides[get_schedule_version_repository] = override_schedule_repo
    app.dependency_overrides[get_generate_schedule_service] = override_generate_service
    return TestClient(app, raise_server_exceptions=raise_server_exceptions)


@pytest.fixture
def client(db):
    _session, session_factory = db
    try:
        yield _client(session_factory)
    finally:
        app.dependency_overrides.pop(get_schedule_version_repository, None)
        app.dependency_overrides.pop(get_generate_schedule_service, None)


def _counts(session: Session, academic_year_id: int) -> dict[str, int]:
    def _count(model):
        return len(
            session.execute(select(model).where(model.academic_year_id == academic_year_id)).scalars().all()
        )

    return {
        "schedule": _count(m.Schedule),
        "schedule_version": _count(m.ScheduleVersion),
        "schedule_entry": _count(m.ScheduleEntry),
        "locked_occurrence": _count(m.LockedOccurrence),
    }


def _year_id(session: Session, academic_year_natural_id: str) -> int:
    return session.execute(
        select(m.AcademicYear.id).where(m.AcademicYear.natural_id == academic_year_natural_id)
    ).scalar_one()


def _seed(session: Session):
    problem = build_valid_fixture()
    write_scheduling_problem(session, problem)
    session.flush()
    return problem


def _expected_entry_count(problem: SchedulingProblem) -> int:
    """The exact number of `ScheduleEntry` rows a successful generation
    persists for `problem`, derived from the domain/solver invariants
    `model_builder.py`/`result_builder.py` actually guarantee (confirmed
    directly, not assumed): `_add_weekly_fulfillment` is a HARD
    constraint applied unconditionally to every `TeachingRequirement`
    (including every split-group branch, which is not deduplicated here
    the way class-occupancy is) -- `sum(lesson_vars for that requirement)
    == requirement.weekly_periods`, and `result_builder.build_schedule_entries`
    emits exactly one entry per placed (day, period) per requirement, so
    each requirement contributes exactly its own `weekly_periods` many
    entries, with no cross-requirement deduplication. Each
    `ReservedBlock` is not solved for at all -- it deterministically
    contributes exactly `len(block.slots)` entries. `FixedPlacement`
    only constrains *where* one of a requirement's own `weekly_periods`
    lessons lands; it does not add an entry of its own."""
    return (
        sum(r.weekly_periods for r in problem.teaching_requirements)
        + sum(len(b.slots) for b in problem.reserved_blocks)
    )


def test_post_generate_success_full_contract(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.post(f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/generate")

    assert response.status_code == 201
    assert "location" not in {k.lower() for k in response.headers.keys()}
    body = response.json()
    assert set(body.keys()) == {"version_number", "solver_status", "total_soft_penalty", "created_at", "is_active"}
    assert body["version_number"] == 1
    assert body["solver_status"] in ("OPTIMAL", "FEASIBLE")
    assert body["is_active"] is True
    assert "entries" not in body
    assert "wall_time_seconds" not in body
    assert "random_seed" not in body

    year_id = _year_id(session, problem.academic_year.id)
    counts = _counts(session, year_id)
    assert counts["schedule"] == 1
    assert counts["schedule_version"] == 1
    assert counts["schedule_entry"] == _expected_entry_count(problem)
    assert counts["locked_occurrence"] == 0


def test_post_generate_has_no_request_body_in_openapi_schema():
    schema = app.openapi()
    operation = schema["paths"]["/schools/{school_id}/years/{year_id}/schedule/generate"]["post"]
    assert "requestBody" not in operation


def test_post_generate_duplicate_returns_schedule_already_exists(client, db):
    session, _session_factory = db
    problem = _seed(session)
    path = f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/generate"

    first = client.post(path)
    assert first.status_code == 201

    second = client.post(path)
    assert second.status_code == 409
    assert second.json() == {
        "code": "SCHEDULE_ALREADY_EXISTS",
        "detail": "A schedule already exists for this school and academic year",
    }

    year_id = _year_id(session, problem.academic_year.id)
    counts = _counts(session, year_id)
    assert counts["schedule"] == 1
    assert counts["schedule_version"] == 1


def test_post_generate_invalid_configuration(client, db):
    session, _session_factory = db
    problem = build_impossible_fixture()
    write_scheduling_problem(session, problem)
    session.flush()
    expected_errors = run_preflight(problem)
    assert expected_errors

    response = client.post(f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/generate")

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "INVALID_CONFIGURATION"
    assert body["detail"] == "Scheduling configuration is invalid"
    assert [e["code"] for e in body["errors"]] == [e.code for e in expected_errors]
    assert [e["message"] for e in body["errors"]] == [e.message for e in expected_errors]

    year_id = _year_id(session, problem.academic_year.id)
    assert _counts(session, year_id) == {
        "schedule": 0, "schedule_version": 0, "schedule_entry": 0, "locked_occurrence": 0,
    }


def test_post_generate_infeasible(client, db):
    session, _session_factory = db
    problem = build_school_scale_impossible()
    write_scheduling_problem(session, problem)
    session.flush()

    response = client.post(f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/generate")

    assert response.status_code == 409
    assert response.json() == {
        "code": "SCHEDULE_INFEASIBLE",
        "detail": "No feasible schedule exists for this school and academic year",
    }

    year_id = _year_id(session, problem.academic_year.id)
    assert _counts(session, year_id) == {
        "schedule": 0, "schedule_version": 0, "schedule_entry": 0, "locked_occurrence": 0,
    }


def test_post_generate_internal_solver_error_does_not_leak_details(db, monkeypatch):
    session, session_factory = db
    problem = _seed(session)

    secret = "SECRET-SOLVER-INTERNAL-9f3c2a"
    monkeypatch.setattr(
        svc_mod, "solve",
        lambda p, o: SchedulingResult(status=SolverStatus.ERROR, metadata={"error": secret}),
    )

    client = _client(session_factory, raise_server_exceptions=False)
    try:
        response = client.post(
            f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/generate"
        )
        assert response.status_code == 500
        assert secret not in response.text
    finally:
        app.dependency_overrides.pop(get_schedule_version_repository, None)
        app.dependency_overrides.pop(get_generate_schedule_service, None)

    year_id = _year_id(session, problem.academic_year.id)
    assert _counts(session, year_id) == {
        "schedule": 0, "schedule_version": 0, "schedule_entry": 0, "locked_occurrence": 0,
    }


def test_post_generate_verifier_failure_does_not_leak_violations(db, monkeypatch):
    session, session_factory = db
    problem = _seed(session)

    secret = "SECRET-VERIFIER-VIOLATION-7b1e0d"
    monkeypatch.setattr(svc_mod, "verify", lambda p, entries: VerificationReport(passed=False, violations=(secret,)))

    client = _client(session_factory, raise_server_exceptions=False)
    try:
        response = client.post(
            f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/generate"
        )
        assert response.status_code == 500
        assert secret not in response.text
    finally:
        app.dependency_overrides.pop(get_schedule_version_repository, None)
        app.dependency_overrides.pop(get_generate_schedule_service, None)

    year_id = _year_id(session, problem.academic_year.id)
    assert _counts(session, year_id) == {
        "schedule": 0, "schedule_version": 0, "schedule_entry": 0, "locked_occurrence": 0,
    }


def test_get_active_success_full_contract(client, db):
    session, session_factory = db
    problem = _seed(session)

    generate_response = client.post(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/generate"
    )
    assert generate_response.status_code == 201

    response = client.get(f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/active")
    assert response.status_code == 200
    body = response.json()

    assert set(body.keys()) == {
        "version_number", "solver_status", "total_soft_penalty", "created_at", "is_active", "entries",
        "locked_occurrences",
    }
    assert body["version_number"] == 1
    assert body["is_active"] is True
    assert "wall_time_seconds" not in body
    assert "random_seed" not in body
    assert body["locked_occurrences"] == []  # a freshly generated version has no locks
    expected_count = _expected_entry_count(problem)
    assert len(body["entries"]) == expected_count

    entry_keys = {
        "source", "day_id", "period_id", "requirement_id", "reserved_block_id",
        "activity_id", "teacher_id", "participant_group_id", "resource_id", "class_sections",
    }
    for entry in body["entries"]:
        assert set(entry.keys()) == entry_keys
        assert "ordinal" not in entry
        assert (entry["requirement_id"] is not None) != (entry["reserved_block_id"] is not None)
        if entry["source"] == "REQUIREMENT":
            assert entry["requirement_id"] is not None
            assert entry["reserved_block_id"] is None
        else:
            assert entry["source"] == "RESERVED_BLOCK"
            assert entry["reserved_block_id"] is not None
            assert entry["requirement_id"] is None

    # Deterministic persisted order proof (repeat GET, and neither
    # trivially sorted nor accidentally reversed).
    response_again = client.get(f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/active")
    assert response_again.json()["entries"] == body["entries"]
    assert list(reversed(body["entries"])) != body["entries"]

    # Exact repository -> HTTP round-trip proof: reload the SAME
    # persisted version through the production, session-factory-backed
    # repository directly (test-only verification -- the production
    # serializer/route never do this reload themselves), serialize its
    # entries through the exact same pure serializer the route uses,
    # and assert byte-for-byte equality with the HTTP response, in the
    # same order -- not merely "same length" or "internally consistent".
    reloaded = SqlAlchemyScheduleVersionRepository(session_factory).get_active_schedule(
        problem.school.id, problem.academic_year.id
    )
    assert reloaded is not None
    assert len(reloaded.entries) == expected_count
    reloaded_entries_as_json = [
        schedule_entry_response_from_entry(e).model_dump(mode="json") for e in reloaded.entries
    ]
    assert reloaded_entries_as_json == body["entries"]


def test_get_unknown_school_year_returns_config_not_found(client):
    response = client.get("/schools/no-such-school/years/no-such-year/schedule/active")
    assert response.status_code == 404
    assert response.json() == {"detail": "Scheduling configuration not found"}


def test_get_valid_config_no_schedule_returns_active_schedule_not_found(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.get(f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/active")
    assert response.status_code == 404
    assert response.json() == {"detail": "Active schedule not found"}


def test_get_active_corrupt_schedule_state_does_not_leak_internal_detail(db):
    session, session_factory = db
    problem = _seed(session)
    year_id = _year_id(session, problem.academic_year.id)

    # Test-only corruption: insert a bare `Schedule` row directly via the
    # ORM, bypassing generation entirely, so `active_version_id` stays
    # NULL outside any atomic creation window -- exactly the corrupt
    # state the route must never leak details about.
    session.add(m.Schedule(academic_year_id=year_id, active_version_id=None))
    session.flush()

    client = _client(session_factory, raise_server_exceptions=False)
    try:
        response = client.get(f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/active")
        assert response.status_code == 500
        assert "active_version_id" not in response.text
        assert "CorruptScheduleStateError" not in response.text
    finally:
        app.dependency_overrides.pop(get_schedule_version_repository, None)
        app.dependency_overrides.pop(get_generate_schedule_service, None)
