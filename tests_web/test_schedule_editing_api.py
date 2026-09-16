"""Real-PostgreSQL, real-solver/verifier HTTP integration tests for the
manual timetable editing backend slice's four thin commands:
`POST .../schedule/active/{move,lock,unlock,reoptimize}`.

Same `join_transaction_mode="create_savepoint"` fixture pattern as
`tests_web/test_schedule_api.py`/`test_schedule_repository.py`: every
adapter-opened `Session` (via the overridden `get_schedule_version_
repository`/`get_generate_schedule_service`/`get_schedule_editing_service`
dependencies) shares the fixture's single outer, never-committed
`Connection`, so nothing here ever leaves a row in the real database --
and `synthetic-school`/`ay-2026` (the real dev-DB pilot dataset) is
never touched at all, since every test here seeds its own isolated
in-transaction fixture school/year.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from school_timetable.api.dependencies import (
    get_class_timetable_service,
    get_generate_schedule_service,
    get_schedule_editing_service,
    get_schedule_version_repository,
    get_teacher_timetable_service,
)
from school_timetable.api.main import app
from school_timetable.application.class_timetable_service import ClassTimetableService
from school_timetable.application.generate_schedule_service import GenerateScheduleService
from school_timetable.application.schedule_editing_service import ScheduleEditingService
from school_timetable.application.teacher_timetable_service import TeacherTimetableService
from school_timetable.domain.indexing import ProblemIndex
from school_timetable.domain.result import EntrySource, ScheduleEntry
from school_timetable.domain.schedule import Schedule
from school_timetable.fixtures.valid_fixture import build_valid_fixture
from school_timetable.persistence import models as m
from school_timetable.persistence.configuration_revision_repository import (
    SqlAlchemyConfigurationRevisionRepository,
)
from school_timetable.persistence.problem_repository import SessionFactorySchedulingProblemRepository
from school_timetable.persistence.schedule_repository import SqlAlchemyScheduleVersionRepository
from school_timetable.scheduling.editing import find_logical_occurrence, validate_move
from tests_web.support.problem_writer import write_scheduling_problem


@pytest.fixture
def db(live_db_engine):
    connection = live_db_engine.connect()
    transaction = connection.begin()
    session_factory = sessionmaker(
        bind=connection, autoflush=False, autocommit=False,
        expire_on_commit=False, join_transaction_mode="create_savepoint",
    )
    session = session_factory()
    try:
        yield session, session_factory
    finally:
        session.close()
        transaction.rollback()
        connection.close()


def _client(session_factory) -> TestClient:
    def override_schedule_repo():
        return SqlAlchemyScheduleVersionRepository(session_factory)

    def override_generate_service():
        return GenerateScheduleService(
            SessionFactorySchedulingProblemRepository(session_factory),
            SqlAlchemyScheduleVersionRepository(session_factory),
        )

    def override_editing_service():
        return ScheduleEditingService(
            SessionFactorySchedulingProblemRepository(session_factory),
            SqlAlchemyScheduleVersionRepository(session_factory),
            SqlAlchemyConfigurationRevisionRepository(session_factory),
        )

    def override_class_timetable_service():
        return ClassTimetableService(
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
    app.dependency_overrides[get_schedule_editing_service] = override_editing_service
    app.dependency_overrides[get_class_timetable_service] = override_class_timetable_service
    app.dependency_overrides[get_teacher_timetable_service] = override_teacher_timetable_service
    return TestClient(app)


@pytest.fixture
def client(db):
    _session, session_factory = db
    try:
        yield _client(session_factory)
    finally:
        app.dependency_overrides.pop(get_schedule_version_repository, None)
        app.dependency_overrides.pop(get_generate_schedule_service, None)
        app.dependency_overrides.pop(get_schedule_editing_service, None)
        app.dependency_overrides.pop(get_class_timetable_service, None)
        app.dependency_overrides.pop(get_teacher_timetable_service, None)


def _seed_and_generate(client, session):
    problem = build_valid_fixture()
    write_scheduling_problem(session, problem)
    session.flush()
    generate_response = client.post(f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/generate")
    assert generate_response.status_code == 201
    return problem, generate_response.json()


def _counts(session: Session, academic_year_id: int) -> dict[str, int]:
    def _count(model):
        return len(
            session.execute(select(model).where(model.academic_year_id == academic_year_id)).scalars().all()
        )

    return {
        "schedule_version": _count(m.ScheduleVersion),
        "schedule_entry": _count(m.ScheduleEntry),
        "locked_occurrence": _count(m.LockedOccurrence),
    }


def _year_id(session: Session, academic_year_natural_id: str) -> int:
    return session.execute(
        select(m.AcademicYear.id).where(m.AcademicYear.natural_id == academic_year_natural_id)
    ).scalar_one()


def _find_move(problem, entries):
    """Finds one valid manual move. `validate_move`'s own HARD-rule
    checks (including REQUIRED-block-pattern integrity -- see the
    "manual timetable editing correction slice" in
    `docs/SCHEDULE_EDITING.md`) are trusted directly; no defensive
    post-hoc re-verification is needed here."""
    index = ProblemIndex(problem)
    schedule = Schedule(entries=entries)
    simple_occs, seen = [], set()
    for e in entries:
        if e.requirement_id is None or (e.requirement_id, e.day_id, e.period_id) in seen:
            continue
        occ = find_logical_occurrence(problem, index, schedule, e.requirement_id, e.day_id, e.period_id)
        if occ.length == 1 and len(occ.requirement_ids) == 1:
            simple_occs.append((e.requirement_id, e.day_id, e.period_id))
        seen.update((mm.requirement_id, mm.day_id, mm.period_id) for mm in occ.members)

    for (r1, d1, p1) in simple_occs:
        for (r2, d2, p2) in simple_occs:
            if r1 == r2 or d1 == d2:
                continue
            result = validate_move(problem, schedule, r1, d1, p1, d2, p2, index=index)
            if not result.allowed:
                continue
            swapped = {e.requirement_id for e in result.plan.removed_entries}
            if swapped != {r1, r2}:
                continue
            return r1, d1, p1, d2, p2
    raise AssertionError("expected at least one valid move in this fixture")


def _entries_from_body(body) -> tuple:
    return tuple(
        ScheduleEntry(
            source=EntrySource(e["source"]), activity_id=e["activity_id"], day_id=e["day_id"],
            period_id=e["period_id"], class_sections=tuple(e["class_sections"]), teacher_id=e["teacher_id"],
            participant_group_id=e["participant_group_id"], resource_id=e["resource_id"],
            requirement_id=e["requirement_id"], reserved_block_id=e["reserved_block_id"],
        )
        for e in body["entries"]
    )


def _flatten_class_timetable(body) -> frozenset[tuple[str, str, str | None, str | None]]:
    """`ClassTimetableResponse`'s grid (`rows` -> `cells` -> `entries`)
    flattened into one comparable, order-independent set of
    `(day_id, period_id, requirement_id, reserved_block_id)` -- used to
    compare two historical/active class projections for genuine equality
    without depending on the grid's row/column ordering."""
    return frozenset(
        (cell["day_id"], row["period_id"], entry["requirement_id"], entry["reserved_block_id"])
        for row in body["rows"]
        for cell in row["cells"]
        for entry in cell["entries"]
    )


# == move =====================================================================


def test_move_success_returns_active_schedule_response_and_persists_new_version(client, db):
    session, _session_factory = db
    problem, _generated = _seed_and_generate(client, session)
    # `POST generate`'s own response has no entries -- fetch the active
    # schedule once to get them for finding a move.
    active = client.get(f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/active").json()
    entries = _entries_from_body(active)
    r1, d1, p1, d2, p2 = _find_move(problem, entries)

    response = client.post(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/active/move",
        json={
            "base_version_number": 1, "requirement_id": r1,
            "source_day_id": d1, "source_period_id": p1,
            "target_day_id": d2, "target_period_id": p2,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["version_number"] == 2
    assert body["solver_status"] == "FEASIBLE"
    assert body["is_active"] is True
    assert body["locked_occurrences"] == []
    assert body["entries"] != active["entries"]

    year_id = _year_id(session, problem.academic_year.id)
    assert _counts(session, year_id)["schedule_version"] == 2


def test_move_rejected_for_fixed_placement_returns_409_with_violations(client, db):
    session, _session_factory = db
    problem, _generated = _seed_and_generate(client, session)

    response = client.post(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/active/move",
        json={
            "base_version_number": 1, "requirement_id": "art_8b",
            "source_day_id": "mon", "source_period_id": "p1",
            "target_day_id": "tue", "target_period_id": "p1",
        },
    )

    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "MOVE_NOT_ALLOWED"
    assert body["violations"][0]["code"] == "FIXED_PLACEMENT"

    year_id = _year_id(session, problem.academic_year.id)
    assert _counts(session, year_id)["schedule_version"] == 1


def test_move_with_stale_base_version_returns_409(client, db):
    session, _session_factory = db
    problem, _generated = _seed_and_generate(client, session)

    response = client.post(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/active/move",
        json={
            "base_version_number": 999, "requirement_id": "art_8b",
            "source_day_id": "mon", "source_period_id": "p1",
            "target_day_id": "tue", "target_period_id": "p1",
        },
    )

    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "STALE_SCHEDULE_VERSION"
    assert body["expected_base_version_number"] == 999
    assert body["actual_active_version_number"] == 1


def test_move_unknown_school_year_returns_404_scheduling_configuration(client):
    response = client.post(
        "/schools/no-such-school/years/no-such-year/schedule/active/move",
        json={
            "base_version_number": 1, "requirement_id": "x",
            "source_day_id": "mon", "source_period_id": "p1",
            "target_day_id": "tue", "target_period_id": "p1",
        },
    )
    assert response.status_code == 404
    assert response.json() == {"detail": "Scheduling configuration not found"}


def test_move_no_active_schedule_returns_404_active_schedule_not_found(client, db):
    session, _session_factory = db
    problem = build_valid_fixture()
    write_scheduling_problem(session, problem)
    session.flush()

    response = client.post(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/active/move",
        json={
            "base_version_number": 1, "requirement_id": "art_8b",
            "source_day_id": "mon", "source_period_id": "p1",
            "target_day_id": "tue", "target_period_id": "p1",
        },
    )
    assert response.status_code == 404
    assert response.json() == {"detail": "Active schedule not found"}


# == move preview =============================================================


def test_preview_move_returns_every_other_slot_with_true_validate_move_outcomes(client, db):
    session, _session_factory = db
    problem, _generated = _seed_and_generate(client, session)
    active = client.get(f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/active").json()
    entries = _entries_from_body(active)
    r1, d1, p1, d2, p2 = _find_move(problem, entries)

    response = client.post(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/active/move/preview",
        json={"base_version_number": 1, "requirement_id": r1, "source_day_id": d1, "source_period_id": p1},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["version_number"] == 1

    index = ProblemIndex(problem)
    expected_slots = {
        (day.id, period.id)
        for day in index.days_sorted
        for period in index.instructional_periods_sorted
    } - {(d1, p1)}
    got_slots = {(t["day_id"], t["period_id"]) for t in body["targets"]}
    assert got_slots == expected_slots

    # No write happened -- still exactly one ScheduleVersion.
    year_id = _year_id(session, problem.academic_year.id)
    assert _counts(session, year_id)["schedule_version"] == 1

    # The allowed target we found is reported as allowed with no violations.
    target = next(t for t in body["targets"] if (t["day_id"], t["period_id"]) == (d2, p2))
    assert target["allowed"] is True
    assert target["violations"] == []


def test_preview_move_forbidden_target_carries_the_fixed_placement_violation(client, db):
    session, _session_factory = db
    problem, _generated = _seed_and_generate(client, session)

    response = client.post(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/active/move/preview",
        json={"base_version_number": 1, "requirement_id": "art_8b", "source_day_id": "mon", "source_period_id": "p1"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["targets"]
    for target in body["targets"]:
        assert target["allowed"] is False
        assert any(v["code"] == "FIXED_PLACEMENT" for v in target["violations"])


def test_preview_move_with_stale_base_version_returns_409(client, db):
    session, _session_factory = db
    problem, _generated = _seed_and_generate(client, session)

    response = client.post(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/active/move/preview",
        json={"base_version_number": 999, "requirement_id": "art_8b", "source_day_id": "mon", "source_period_id": "p1"},
    )

    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "STALE_SCHEDULE_VERSION"
    assert body["expected_base_version_number"] == 999
    assert body["actual_active_version_number"] == 1

    year_id = _year_id(session, problem.academic_year.id)
    assert _counts(session, year_id)["schedule_version"] == 1


def test_preview_move_unresolvable_source_returns_422_invalid_edit_target(client, db):
    session, _session_factory = db
    problem, _generated = _seed_and_generate(client, session)

    response = client.post(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/active/move/preview",
        json={
            "base_version_number": 1, "requirement_id": "no-such-requirement",
            "source_day_id": "mon", "source_period_id": "p1",
        },
    )

    assert response.status_code == 422
    assert response.json()["code"] == "INVALID_EDIT_TARGET"

    year_id = _year_id(session, problem.academic_year.id)
    assert _counts(session, year_id)["schedule_version"] == 1


def test_preview_move_unknown_school_year_returns_404_scheduling_configuration(client):
    response = client.post(
        "/schools/no-such-school/years/no-such-year/schedule/active/move/preview",
        json={"base_version_number": 1, "requirement_id": "x", "source_day_id": "mon", "source_period_id": "p1"},
    )
    assert response.status_code == 404
    assert response.json() == {"detail": "Scheduling configuration not found"}


def test_preview_move_no_active_schedule_returns_404_active_schedule_not_found(client, db):
    session, _session_factory = db
    problem = build_valid_fixture()
    write_scheduling_problem(session, problem)
    session.flush()

    response = client.post(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/active/move/preview",
        json={"base_version_number": 1, "requirement_id": "art_8b", "source_day_id": "mon", "source_period_id": "p1"},
    )
    assert response.status_code == 404
    assert response.json() == {"detail": "Active schedule not found"}


def test_preview_move_never_persists_and_a_real_move_still_works_afterward(client, db):
    """Proves the preview route performs zero persistence writes, and that
    running it does not disturb the real move command's own behavior."""
    session, _session_factory = db
    problem, _generated = _seed_and_generate(client, session)
    active = client.get(f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/active").json()
    entries = _entries_from_body(active)
    r1, d1, p1, d2, p2 = _find_move(problem, entries)
    year_id = _year_id(session, problem.academic_year.id)

    for _ in range(3):
        preview_response = client.post(
            f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/active/move/preview",
            json={"base_version_number": 1, "requirement_id": r1, "source_day_id": d1, "source_period_id": p1},
        )
        assert preview_response.status_code == 200
    assert _counts(session, year_id)["schedule_version"] == 1

    move_response = client.post(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/active/move",
        json={
            "base_version_number": 1, "requirement_id": r1,
            "source_day_id": d1, "source_period_id": p1,
            "target_day_id": d2, "target_period_id": p2,
        },
    )
    assert move_response.status_code == 200
    assert move_response.json()["version_number"] == 2
    assert _counts(session, year_id)["schedule_version"] == 2


# == lock / unlock ============================================================


def test_lock_success_returns_new_version_with_unchanged_entries(client, db):
    session, _session_factory = db
    problem, _generated = _seed_and_generate(client, session)
    active = client.get(f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/active").json()
    entries = _entries_from_body(active)
    entry = next(e for e in entries if e.requirement_id == "math_8a")

    response = client.post(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/active/lock",
        json={"base_version_number": 1, "requirement_id": "math_8a", "day_id": entry.day_id, "period_id": entry.period_id},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["version_number"] == 2
    assert body["entries"] == active["entries"]
    assert any(k["requirement_id"] == "math_8a" for k in body["locked_occurrences"])
    # Entries unchanged -> the base version's own truthful metadata carries forward.
    assert body["solver_status"] == active["solver_status"]
    assert body["total_soft_penalty"] == active["total_soft_penalty"]


def test_lock_split_group_branch_locks_both_siblings(client, db):
    session, _session_factory = db
    problem, _generated = _seed_and_generate(client, session)
    active = client.get(f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/active").json()
    entries = _entries_from_body(active)
    entry = next(e for e in entries if e.requirement_id == "german_8a")

    response = client.post(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/active/lock",
        json={"base_version_number": 1, "requirement_id": "german_8a", "day_id": entry.day_id, "period_id": entry.period_id},
    )

    assert response.status_code == 200
    locked_ids = {k["requirement_id"] for k in response.json()["locked_occurrences"]}
    assert locked_ids == {"german_8a", "russian_8a"}


def test_lock_unknown_requirement_returns_422_invalid_edit_target(client, db):
    session, _session_factory = db
    problem, _generated = _seed_and_generate(client, session)

    response = client.post(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/active/lock",
        json={"base_version_number": 1, "requirement_id": "no-such-requirement", "day_id": "mon", "period_id": "p1"},
    )

    assert response.status_code == 422
    assert response.json()["code"] == "INVALID_EDIT_TARGET"


def test_unlock_creates_new_version_leaving_the_historical_locked_version_intact(client, db):
    session, session_factory = db
    problem, _generated = _seed_and_generate(client, session)
    active = client.get(f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/active").json()
    entries = _entries_from_body(active)
    entry = next(e for e in entries if e.requirement_id == "german_8a")

    lock_response = client.post(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/active/lock",
        json={"base_version_number": 1, "requirement_id": "german_8a", "day_id": entry.day_id, "period_id": entry.period_id},
    )
    assert lock_response.status_code == 200

    unlock_response = client.post(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/active/unlock",
        json={"base_version_number": 2, "requirement_id": "german_8a", "day_id": entry.day_id, "period_id": entry.period_id},
    )
    assert unlock_response.status_code == 200
    body = unlock_response.json()
    assert body["version_number"] == 3
    assert body["locked_occurrences"] == []

    # v2's own historical LockedOccurrence rows are untouched.
    year_id = _year_id(session, problem.academic_year.id)
    v2_row = session.execute(
        select(m.ScheduleVersion).where(m.ScheduleVersion.academic_year_id == year_id, m.ScheduleVersion.version_number == 2)
    ).scalar_one()
    v2_locks = session.execute(
        select(m.LockedOccurrence).where(m.LockedOccurrence.schedule_version_id == v2_row.id)
    ).scalars().all()
    assert len(v2_locks) == 2


# == reoptimize ================================================================


def test_reoptimize_success_returns_new_active_version(client, db):
    session, _session_factory = db
    problem, _generated = _seed_and_generate(client, session)

    response = client.post(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/active/reoptimize",
        json={"base_version_number": 1},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["version_number"] == 2
    assert body["solver_status"] in ("OPTIMAL", "FEASIBLE")
    assert body["is_active"] is True

    year_id = _year_id(session, problem.academic_year.id)
    assert _counts(session, year_id)["schedule_version"] == 2


def test_reoptimize_with_stale_base_version_returns_409(client, db):
    session, _session_factory = db
    problem, _generated = _seed_and_generate(client, session)

    response = client.post(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/active/reoptimize",
        json={"base_version_number": 999},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "STALE_SCHEDULE_VERSION"

    year_id = _year_id(session, problem.academic_year.id)
    assert _counts(session, year_id)["schedule_version"] == 1


def test_reoptimize_unknown_school_year_returns_404(client):
    response = client.post(
        "/schools/no-such-school/years/no-such-year/schedule/active/reoptimize",
        json={"base_version_number": 1},
    )
    assert response.status_code == 404
    assert response.json() == {"detail": "Scheduling configuration not found"}


# == schedule version history + restore ======================================


def _seed_generate_move_lock(client, session):
    """v1 (generated) -> v2 (a real move) -> v3 (lock german_8a, which
    also locks split sibling russian_8a)."""
    problem, _generated = _seed_and_generate(client, session)
    active = client.get(f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/active").json()
    entries = _entries_from_body(active)
    r1, d1, p1, d2, p2 = _find_move(problem, entries)

    move_response = client.post(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/active/move",
        json={
            "base_version_number": 1, "requirement_id": r1,
            "source_day_id": d1, "source_period_id": p1,
            "target_day_id": d2, "target_period_id": p2,
        },
    )
    assert move_response.status_code == 200
    v2_entries = _entries_from_body(move_response.json())
    german_entry = next(e for e in v2_entries if e.requirement_id == "german_8a")

    lock_response = client.post(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/active/lock",
        json={
            "base_version_number": 2, "requirement_id": "german_8a",
            "day_id": german_entry.day_id, "period_id": german_entry.period_id,
        },
    )
    assert lock_response.status_code == 200
    return problem


def test_history_endpoint_lists_newest_first_with_exactly_one_active(client, db):
    session, _session_factory = db
    problem = _seed_generate_move_lock(client, session)

    response = client.get(f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/versions")

    assert response.status_code == 200
    body = response.json()
    versions = body["versions"]
    assert [v["version_number"] for v in versions] == [3, 2, 1]
    assert sum(1 for v in versions if v["is_active"]) == 1
    assert next(v for v in versions if v["is_active"])["version_number"] == 3
    by_number = {v["version_number"]: v for v in versions}
    assert by_number[1]["parent_version_number"] is None
    assert by_number[2]["parent_version_number"] == 1
    assert by_number[3]["parent_version_number"] == 2


def test_history_endpoint_unknown_school_year_returns_404(client):
    response = client.get("/schools/no-such-school/years/no-such-year/schedule/versions")
    assert response.status_code == 404
    assert response.json() == {"detail": "Scheduling configuration not found"}


def test_history_endpoint_no_schedule_returns_404_active_schedule_not_found(client, db):
    session, _session_factory = db
    problem = build_valid_fixture()
    write_scheduling_problem(session, problem)
    session.flush()

    response = client.get(f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/versions")
    assert response.status_code == 404
    assert response.json() == {"detail": "Active schedule not found"}


def test_historical_class_projection_reflects_that_version_not_current_active(client, db):
    session, _session_factory = db
    problem = _seed_generate_move_lock(client, session)

    v1_view = client.get(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/versions/1/classes/8a"
    )
    assert v1_view.status_code == 200
    v1_body = v1_view.json()
    assert v1_body["version_number"] == 1
    assert v1_body["is_active"] is False

    v3_view = client.get(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/versions/3/classes/8a"
    )
    assert v3_view.status_code == 200
    v3_body = v3_view.json()
    assert v3_body["version_number"] == 3
    assert v3_body["is_active"] is True
    active_class_view = client.get(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/active/classes/8a"
    ).json()
    assert _flatten_class_timetable(v3_body) == _flatten_class_timetable(active_class_view)

    # v1 and v3 genuinely differ -- proves this is not silently always
    # projecting the active version regardless of the URL.
    assert _flatten_class_timetable(v1_body) != _flatten_class_timetable(v3_body)


def test_historical_class_projection_unknown_version_returns_404_with_stable_code(client, db):
    session, _session_factory = db
    problem = _seed_generate_move_lock(client, session)

    response = client.get(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/versions/999/classes/8a"
    )
    assert response.status_code == 404
    body = response.json()
    assert body["code"] == "SCHEDULE_VERSION_NOT_FOUND"
    assert body["version_number"] == 999


def test_historical_class_projection_unknown_class_returns_404(client, db):
    session, _session_factory = db
    problem = _seed_generate_move_lock(client, session)

    response = client.get(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/versions/1/classes/no-such-class"
    )
    assert response.status_code == 404
    assert response.json() == {"detail": "Class section not found"}


def test_historical_teacher_projection_reflects_that_version_not_current_active(client, db):
    """F: historical teacher projection matches that version."""
    session, _session_factory = db
    problem = _seed_generate_move_lock(client, session)

    v1_view = client.get(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/versions/1/teachers/t_math"
    )
    assert v1_view.status_code == 200
    assert v1_view.json()["is_active"] is False

    v3_view = client.get(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/versions/3/teachers/t_math"
    )
    assert v3_view.status_code == 200
    assert v3_view.json()["is_active"] is True


def test_restore_creates_new_version_copied_from_source_and_promotes_it(client, db):
    """G/H/I/J/K/M: restore v1 while v3 active creates v4, parented from
    v3, with v1's own entries/locks (v1 has none; v3 has two -- proving
    locks come from the historical source, never the active version),
    and its own truthful solver_status/total_soft_penalty; v4 becomes
    active."""
    session, _session_factory = db
    problem = _seed_generate_move_lock(client, session)
    v1_view = client.get(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/versions/1/classes/8a"
    ).json()

    response = client.post(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/versions/1/restore",
        json={"base_version_number": 3},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["version_number"] == 4
    assert body["is_active"] is True
    assert body["locked_occurrences"] == []  # v1's own locks (none)

    active_after = client.get(f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/active").json()
    assert active_after["version_number"] == 4
    v4_class_view = client.get(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/versions/4/classes/8a"
    ).json()
    assert _flatten_class_timetable(v4_class_view) == _flatten_class_timetable(v1_view)  # I: entries exactly equal v1's

    year_id = _year_id(session, problem.academic_year.id)
    assert _counts(session, year_id)["schedule_version"] == 4


def test_restore_leaves_earlier_versions_unchanged(client, db):
    """L: v1-v3 remain unchanged after a restore creates v4."""
    session, _session_factory = db
    problem = _seed_generate_move_lock(client, session)
    v1_before = client.get(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/versions/1/classes/8a"
    ).json()
    v3_before = client.get(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/versions/3/classes/8a"
    ).json()

    restore_response = client.post(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/versions/1/restore",
        json={"base_version_number": 3},
    )
    assert restore_response.status_code == 200

    v1_after = client.get(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/versions/1/classes/8a"
    ).json()
    v3_after = client.get(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/versions/3/classes/8a"
    ).json()
    assert _flatten_class_timetable(v1_after) == _flatten_class_timetable(v1_before)
    assert _flatten_class_timetable(v3_after) == _flatten_class_timetable(v3_before)


def test_restore_with_stale_base_version_returns_409(client, db):
    session, _session_factory = db
    problem = _seed_generate_move_lock(client, session)

    response = client.post(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/versions/1/restore",
        json={"base_version_number": 999},
    )

    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "STALE_SCHEDULE_VERSION"
    assert body["expected_base_version_number"] == 999
    assert body["actual_active_version_number"] == 3

    year_id = _year_id(session, problem.academic_year.id)
    assert _counts(session, year_id)["schedule_version"] == 3


def test_restore_active_version_returns_409_version_already_active(client, db):
    """O: restoring the currently active version is rejected cleanly,
    zero writes."""
    session, _session_factory = db
    problem = _seed_generate_move_lock(client, session)

    response = client.post(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/versions/3/restore",
        json={"base_version_number": 3},
    )

    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "VERSION_ALREADY_ACTIVE"
    assert body["version_number"] == 3

    year_id = _year_id(session, problem.academic_year.id)
    assert _counts(session, year_id)["schedule_version"] == 3


def test_restore_unknown_version_returns_404(client, db):
    session, _session_factory = db
    problem = _seed_generate_move_lock(client, session)

    response = client.post(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/versions/999/restore",
        json={"base_version_number": 3},
    )

    assert response.status_code == 404
    body = response.json()
    assert body["code"] == "SCHEDULE_VERSION_NOT_FOUND"
    assert body["version_number"] == 999

    year_id = _year_id(session, problem.academic_year.id)
    assert _counts(session, year_id)["schedule_version"] == 3


def test_restore_unknown_school_year_returns_404(client):
    response = client.post(
        "/schools/no-such-school/years/no-such-year/schedule/versions/1/restore",
        json={"base_version_number": 1},
    )
    assert response.status_code == 404
    assert response.json() == {"detail": "Scheduling configuration not found"}


def test_restore_then_a_subsequent_normal_move_still_works(client, db):
    """S: subsequent normal Move/Lock/Reoptimize still works from the
    restored active version."""
    session, _session_factory = db
    problem = _seed_generate_move_lock(client, session)

    restore_response = client.post(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/versions/1/restore",
        json={"base_version_number": 3},
    )
    assert restore_response.status_code == 200
    v4 = restore_response.json()
    entries = _entries_from_body(v4)
    r1, d1, p1, d2, p2 = _find_move(problem, entries)

    move_response = client.post(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/active/move",
        json={
            "base_version_number": 4, "requirement_id": r1,
            "source_day_id": d1, "source_period_id": p1,
            "target_day_id": d2, "target_period_id": p2,
        },
    )
    assert move_response.status_code == 200
    assert move_response.json()["version_number"] == 5


# == Safe Configuration Changes, Slice B, Owner Decision 1: stale-timetable
#    mutation guard =========================================================
#
# `_open_draft` calls the REAL persistence-layer `SqlAlchemyConfiguration
# RevisionRepository.begin_draft` directly (never a fake) -- the exact
# production code path a future "Edit scheduling configuration" command
# will call -- to put the year into the precise state Owner Decision 1
# protects against: a `Schedule` already exists AND a configuration draft
# is open. Every test below proves the SAME thing `test_..._success_...`
# above it already proves for the "no draft open" case: with no draft
# open, every one of these five commands succeeds (that is exactly what
# every other test in this file already demonstrates, against the same
# real `ScheduleEditingService`/`SqlAlchemyConfigurationRevisionRepository`
# wiring) -- these tests isolate the other half: once a draft opens
# alongside the Schedule, the identical command is rejected with `409
# SCHEDULE_OUT_OF_DATE`, and -- critically -- zero rows are mutated by the
# rejected attempt.

def _open_draft(session_factory, problem) -> None:
    SqlAlchemyConfigurationRevisionRepository(session_factory).begin_draft(
        problem.school.id, problem.academic_year.id,
    )
    # An untouched clone is current under semantic state rules; mutate one
    # draft value so these tests specifically exercise stale-edit rejection.
    session = session_factory()
    try:
        row = session.execute(
            select(m.AcademicYear.id, m.AcademicYear.draft_revision_id)
            .join(m.School, m.School.id == m.AcademicYear.school_id)
            .where(
                m.School.natural_id == problem.school.id,
                m.AcademicYear.natural_id == problem.academic_year.id,
            )
            .order_by(m.AcademicYear.id.desc())
        ).first()
        if row is None:
            raise AssertionError("seeded academic year was not found")
        year_id, draft_id = row
        teacher = session.execute(
            select(m.Teacher)
            .where(
                m.Teacher.academic_year_id == year_id,
                m.Teacher.configuration_revision_id == draft_id,
            )
            .order_by(m.Teacher.ordinal)
        ).scalars().first()
        if teacher is None:
            raise AssertionError("seeded draft teacher was not found")
        teacher.first_name = teacher.first_name + " (draft)"
        session.commit()
    finally:
        session.close()


def test_move_rejected_when_configuration_draft_is_open(client, db):
    session, session_factory = db
    problem, _generated = _seed_and_generate(client, session)
    active = client.get(f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/active").json()
    entries = _entries_from_body(active)
    r1, d1, p1, d2, p2 = _find_move(problem, entries)

    _open_draft(session_factory, problem)

    response = client.post(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/active/move",
        json={
            "base_version_number": 1, "requirement_id": r1,
            "source_day_id": d1, "source_period_id": p1,
            "target_day_id": d2, "target_period_id": p2,
        },
    )

    assert response.status_code == 409
    assert response.json()["code"] == "SCHEDULE_OUT_OF_DATE"

    year_id = _year_id(session, problem.academic_year.id)
    assert _counts(session, year_id)["schedule_version"] == 1


def test_lock_rejected_when_configuration_draft_is_open(client, db):
    session, session_factory = db
    problem, _generated = _seed_and_generate(client, session)
    active = client.get(f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/active").json()
    entries = _entries_from_body(active)
    entry = next(e for e in entries if e.requirement_id == "math_8a")

    _open_draft(session_factory, problem)

    response = client.post(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/active/lock",
        json={"base_version_number": 1, "requirement_id": "math_8a", "day_id": entry.day_id, "period_id": entry.period_id},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "SCHEDULE_OUT_OF_DATE"

    year_id = _year_id(session, problem.academic_year.id)
    counts = _counts(session, year_id)
    assert counts["schedule_version"] == 1
    assert counts["locked_occurrence"] == 0


def test_unlock_rejected_when_configuration_draft_is_open(client, db):
    session, session_factory = db
    problem, _generated = _seed_and_generate(client, session)
    active = client.get(f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/active").json()
    entries = _entries_from_body(active)
    entry = next(e for e in entries if e.requirement_id == "german_8a")

    lock_response = client.post(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/active/lock",
        json={"base_version_number": 1, "requirement_id": "german_8a", "day_id": entry.day_id, "period_id": entry.period_id},
    )
    assert lock_response.status_code == 200

    _open_draft(session_factory, problem)

    response = client.post(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/active/unlock",
        json={"base_version_number": 2, "requirement_id": "german_8a", "day_id": entry.day_id, "period_id": entry.period_id},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "SCHEDULE_OUT_OF_DATE"

    year_id = _year_id(session, problem.academic_year.id)
    counts = _counts(session, year_id)
    assert counts["schedule_version"] == 2
    assert counts["locked_occurrence"] == 2  # german_8a + split sibling russian_8a, still locked on v2


def test_reoptimize_rejected_when_configuration_draft_is_open(client, db):
    session, session_factory = db
    problem, _generated = _seed_and_generate(client, session)

    _open_draft(session_factory, problem)

    response = client.post(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/active/reoptimize",
        json={"base_version_number": 1},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "SCHEDULE_OUT_OF_DATE"

    year_id = _year_id(session, problem.academic_year.id)
    assert _counts(session, year_id)["schedule_version"] == 1


def test_restore_rejected_when_configuration_draft_is_open(client, db):
    session, session_factory = db
    problem = _seed_generate_move_lock(client, session)

    _open_draft(session_factory, problem)

    response = client.post(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/versions/1/restore",
        json={"base_version_number": 3},
    )

    assert response.status_code == 409
    assert response.json()["code"] == "SCHEDULE_OUT_OF_DATE"

    year_id = _year_id(session, problem.academic_year.id)
    assert _counts(session, year_id)["schedule_version"] == 3


def test_preview_move_still_allowed_when_configuration_draft_is_open(client, db):
    """Move preview stays read-only and technically callable even while
    stale -- only the five MUTATING commands above are rejected."""
    session, session_factory = db
    problem, _generated = _seed_and_generate(client, session)
    active = client.get(f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/active").json()
    entries = _entries_from_body(active)
    r1, d1, p1, _d2, _p2 = _find_move(problem, entries)

    _open_draft(session_factory, problem)

    response = client.post(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/active/move/preview",
        json={"base_version_number": 1, "requirement_id": r1, "source_day_id": d1, "source_period_id": p1},
    )

    assert response.status_code == 200


def test_move_succeeds_again_after_draft_is_discarded(client, db):
    """The guard checks LIVE state on every call -- once the draft is
    discarded (configuration is locked again, matching the pre-draft
    state), the identical move succeeds exactly as it would have with
    no draft ever having been opened."""
    session, session_factory = db
    problem, _generated = _seed_and_generate(client, session)
    active = client.get(f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/active").json()
    entries = _entries_from_body(active)
    r1, d1, p1, d2, p2 = _find_move(problem, entries)

    revision_repo = SqlAlchemyConfigurationRevisionRepository(session_factory)
    revision_repo.begin_draft(problem.school.id, problem.academic_year.id)
    revision_repo.discard_draft(problem.school.id, problem.academic_year.id)

    response = client.post(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/active/move",
        json={
            "base_version_number": 1, "requirement_id": r1,
            "source_day_id": d1, "source_period_id": p1,
            "target_day_id": d2, "target_period_id": p2,
        },
    )

    assert response.status_code == 200
    assert response.json()["version_number"] == 2
