"""HTTP integration tests for Safe Configuration Changes, Slice C,
Checkpoint 5: `POST .../schedule/active/regenerate`
(`api/schedule_routes.py::regenerate_schedule`).

Route-contract concerns (schema validation, error-mapping shape,
argument forwarding) use a small in-memory FAKE `GenerateScheduleService`
wired through `app.dependency_overrides`, following this file's own
`test_generate_schedule_service.py` fake-repository discipline lifted up
one layer -- no real database, no real solver, no real CP-SAT model,
matching this checkpoint's explicit "prefer fakes for route-contract
tests" guidance.

The one scenario that genuinely needs a REAL database and a REAL solve
(the exact-confirmation round trip proving a SECOND, different
configuration change between two HTTP calls returns a FRESH 409
classification rather than silently accepting a stale confirmation) uses
the same `join_transaction_mode="create_savepoint"` fixture pattern as
every other `tests_web/test_*_api.py` file.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from school_timetable.api.dependencies import (
    get_configuration_revision_repository,
    get_generate_schedule_service,
    get_schedule_editing_service,
    get_schedule_version_repository,
)
from school_timetable.api.main import app
from school_timetable.application.errors import (
    ConfigurationChangedDuringGenerationError,
    IncompatibleLocksRequireConfirmationError,
    InvalidSchedulingConfigurationError,
    NoActiveScheduleError,
    NoConfigurationDraftError,
    ScheduleInfeasibleError,
    StaleScheduleVersionError,
)
from school_timetable.application.generate_schedule_service import GenerateScheduleService
from school_timetable.application.schedule_editing_service import ScheduleEditingService
from school_timetable.application.schedule_models import ActiveScheduleVersion
from school_timetable.domain.result import EntrySource, ScheduleEntry, SolverStatus
from school_timetable.domain.schedule import OccurrenceKey
from school_timetable.fixtures.valid_fixture import build_valid_fixture
from school_timetable.persistence import models as m
from school_timetable.persistence.configuration_revision_repository import (
    SqlAlchemyConfigurationRevisionRepository,
)
from school_timetable.persistence.problem_repository import SessionFactorySchedulingProblemRepository
from school_timetable.persistence.schedule_repository import SqlAlchemyScheduleVersionRepository
from school_timetable.scheduling.lock_compatibility import TEACHER_UNAVAILABLE_AT_SLOT, IncompatibleLock
from school_timetable.validation.errors import ValidationError
from tests_web.support.problem_writer import write_scheduling_problem

_CREATED_AT = datetime(2024, 1, 1, tzinfo=timezone.utc)
_SCHOOL = "school-1"
_YEAR = "year-1"


class _FakeGenerateScheduleService:
    """Mirrors the fakes in `tests/test_generate_schedule_service.py`,
    lifted to route level: `regenerate` either returns a canned
    `ActiveScheduleVersion` or raises a canned exception, and every call
    is recorded verbatim so a test can assert exactly what the route
    forwarded (Requirement 8's frozenset-deserialization proof)."""

    def __init__(self, active: ActiveScheduleVersion | None = None, error: Exception | None = None):
        self._active = active
        self._error = error
        self.regenerate_calls: list[dict] = []

    def regenerate(
        self,
        school_natural_id,
        academic_year_natural_id,
        base_version_number,
        confirmed_incompatible_lock_keys=frozenset(),
        *,
        solver_options=None,
    ):
        self.regenerate_calls.append({
            "school_natural_id": school_natural_id,
            "academic_year_natural_id": academic_year_natural_id,
            "base_version_number": base_version_number,
            "confirmed_incompatible_lock_keys": confirmed_incompatible_lock_keys,
        })
        if self._error is not None:
            raise self._error
        return self._active


def _fake_client(fake: _FakeGenerateScheduleService) -> TestClient:
    app.dependency_overrides[get_generate_schedule_service] = lambda: fake
    return TestClient(app)


@pytest.fixture
def fake_client():
    fakes: list[_FakeGenerateScheduleService] = []

    def _make(active=None, error=None) -> TestClient:
        fake = _FakeGenerateScheduleService(active=active, error=error)
        fakes.append(fake)
        return _fake_client(fake)

    try:
        yield _make, fakes
    finally:
        app.dependency_overrides.pop(get_generate_schedule_service, None)


def _active_version(**overrides) -> ActiveScheduleVersion:
    defaults = dict(
        version_number=2, solver_status=SolverStatus.OPTIMAL, total_soft_penalty=3,
        wall_time_seconds=1.5, random_seed=None, created_at=_CREATED_AT,
        entries=(
            ScheduleEntry(
                source=EntrySource.REQUIREMENT, activity_id="math", day_id="mon", period_id="p1",
                class_sections=("9a",), teacher_id="t1", participant_group_id="g1",
                requirement_id="req1",
            ),
        ),
        locked_occurrences=frozenset({OccurrenceKey("req1", "mon", "p1")}),
        configuration_revision_number=2,
    )
    defaults.update(overrides)
    return ActiveScheduleVersion(**defaults)


_URL = f"/schools/{_SCHOOL}/years/{_YEAR}/schedule/active/regenerate"


# == 1: successful regenerate =================================================


def test_regenerate_success_returns_new_active_version(fake_client):
    make_client, fakes = fake_client
    active = _active_version()
    client = make_client(active=active)

    response = client.post(_URL, json={"base_version_number": 1})

    assert response.status_code == 200
    body = response.json()
    assert body["version_number"] == 2
    assert body["solver_status"] == "OPTIMAL"
    assert body["total_soft_penalty"] == 3
    assert body["is_active"] is True
    assert body["entries"][0]["requirement_id"] == "req1"
    assert body["locked_occurrences"] == [
        {"requirement_id": "req1", "day_id": "mon", "anchor_period_id": "p1"},
    ]
    assert fakes[0].regenerate_calls == [{
        "school_natural_id": _SCHOOL, "academic_year_natural_id": _YEAR,
        "base_version_number": 1, "confirmed_incompatible_lock_keys": frozenset(),
    }]


# == 2/3: request schema validation ==========================================


def test_regenerate_requires_base_version_number(fake_client):
    make_client, fakes = fake_client
    client = make_client(active=_active_version())

    response = client.post(_URL, json={})

    assert response.status_code == 422
    assert fakes[0].regenerate_calls == []  # never reaches the service


def test_regenerate_rejects_malformed_confirmation_key(fake_client):
    make_client, fakes = fake_client
    client = make_client(active=_active_version())

    # Missing `anchor_period_id` -- ordinary FastAPI/Pydantic request
    # validation must reject this before the route body ever runs.
    response = client.post(_URL, json={
        "base_version_number": 1,
        "confirmed_incompatible_lock_keys": [{"requirement_id": "r1", "day_id": "mon"}],
    })

    assert response.status_code == 422
    assert fakes[0].regenerate_calls == []


def test_regenerate_rejects_bare_confirm_boolean(fake_client):
    """The caller must echo the exact natural-ID key set -- a bare
    `confirm: true` is not a valid substitute and is rejected by
    ordinary schema validation (wrong type for the tuple field)."""
    make_client, fakes = fake_client
    client = make_client(active=_active_version())

    response = client.post(_URL, json={
        "base_version_number": 1, "confirmed_incompatible_lock_keys": True,
    })

    assert response.status_code == 422
    assert fakes[0].regenerate_calls == []


# == 4/5/6: existing error conventions reused ================================


def test_regenerate_no_draft_maps_to_409_no_configuration_draft(fake_client):
    make_client, _ = fake_client
    client = make_client(error=NoConfigurationDraftError(_SCHOOL, _YEAR))

    response = client.post(_URL, json={"base_version_number": 1})

    assert response.status_code == 409
    assert response.json()["code"] == "NO_CONFIGURATION_DRAFT"


def test_regenerate_no_active_schedule_maps_to_404(fake_client):
    make_client, _ = fake_client
    client = make_client(error=NoActiveScheduleError(_SCHOOL, _YEAR))

    response = client.post(_URL, json={"base_version_number": 1})

    assert response.status_code == 404
    assert response.json()["detail"] == "Active schedule not found"


def test_regenerate_stale_base_version_maps_to_409_stale_schedule_version(fake_client):
    make_client, _ = fake_client
    client = make_client(error=StaleScheduleVersionError(_SCHOOL, _YEAR, 1, 3))

    response = client.post(_URL, json={"base_version_number": 1})

    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "STALE_SCHEDULE_VERSION"
    assert body["expected_base_version_number"] == 1
    assert body["actual_active_version_number"] == 3


# == 7/9: incompatible-lock structured error =================================


def test_regenerate_incompatible_locks_without_confirmation_returns_structured_409(fake_client):
    make_client, _ = fake_client
    incompatible = (
        IncompatibleLock(
            key=OccurrenceKey("req1", "mon", "p1"),
            reason_code=TEACHER_UNAVAILABLE_AT_SLOT,
            message="Teacher t1 is unavailable at (mon, p1) in the draft configuration",
        ),
    )
    client = make_client(error=IncompatibleLocksRequireConfirmationError(_SCHOOL, _YEAR, incompatible))

    response = client.post(_URL, json={"base_version_number": 1})

    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "INCOMPATIBLE_LOCKS_REQUIRE_CONFIRMATION"
    assert len(body["incompatible_locks"]) == 1
    lock = body["incompatible_locks"][0]
    assert lock == {
        "requirement_id": "req1", "day_id": "mon", "anchor_period_id": "p1",
        "reason_code": TEACHER_UNAVAILABLE_AT_SLOT,
        "message": "Teacher t1 is unavailable at (mon, p1) in the draft configuration",
    }
    # No database surrogate ID (an integer `id` field) anywhere in the body.
    assert "id" not in lock
    assert all(isinstance(v, str) for v in lock.values())


def test_regenerate_multiple_incompatible_locks_round_trip(fake_client):
    make_client, _ = fake_client
    incompatible = (
        IncompatibleLock(
            key=OccurrenceKey("req1", "mon", "p1"), reason_code="REQUIREMENT_OR_SLOT_DELETED",
            message="Requirement req1 no longer exists in the draft configuration",
        ),
        IncompatibleLock(
            key=OccurrenceKey("req2", "tue", "p2"), reason_code=TEACHER_UNAVAILABLE_AT_SLOT,
            message="Teacher t2 is unavailable at (tue, p2) in the draft configuration",
        ),
    )
    client = make_client(error=IncompatibleLocksRequireConfirmationError(_SCHOOL, _YEAR, incompatible))

    response = client.post(_URL, json={"base_version_number": 1})

    assert response.status_code == 409
    keys = {(lock["requirement_id"], lock["day_id"], lock["anchor_period_id"], lock["reason_code"])
            for lock in response.json()["incompatible_locks"]}
    assert keys == {
        ("req1", "mon", "p1", "REQUIREMENT_OR_SLOT_DELETED"),
        ("req2", "tue", "p2", TEACHER_UNAVAILABLE_AT_SLOT),
    }


# == 8: exact confirmation deserializes into frozenset[OccurrenceKey] =======


def test_regenerate_confirmation_keys_deserialize_to_frozenset_occurrence_key(fake_client):
    make_client, fakes = fake_client
    client = make_client(active=_active_version())

    response = client.post(_URL, json={
        "base_version_number": 1,
        "confirmed_incompatible_lock_keys": [
            {"requirement_id": "req1", "day_id": "mon", "anchor_period_id": "p1"},
            {"requirement_id": "req2", "day_id": "tue", "anchor_period_id": "p2"},
        ],
    })

    assert response.status_code == 200
    forwarded = fakes[0].regenerate_calls[0]["confirmed_incompatible_lock_keys"]
    assert isinstance(forwarded, frozenset)
    assert forwarded == frozenset({
        OccurrenceKey("req1", "mon", "p1"), OccurrenceKey("req2", "tue", "p2"),
    })


# == 11: ConfigurationChangedDuringGenerationError ============================


def test_regenerate_configuration_changed_during_generation_maps_to_409(fake_client):
    make_client, _ = fake_client
    client = make_client(error=ConfigurationChangedDuringGenerationError(_SCHOOL, _YEAR))

    response = client.post(_URL, json={"base_version_number": 1})

    assert response.status_code == 409
    assert response.json()["code"] == "CONFIGURATION_CHANGED_DURING_GENERATION"


# == 12: infeasible/invalid preserve existing generation mapping ============


def test_regenerate_infeasible_maps_to_409_schedule_infeasible(fake_client):
    make_client, _ = fake_client
    client = make_client(error=ScheduleInfeasibleError(_SCHOOL, _YEAR))

    response = client.post(_URL, json={"base_version_number": 1})

    assert response.status_code == 409
    assert response.json()["code"] == "SCHEDULE_INFEASIBLE"


def test_regenerate_invalid_configuration_maps_to_422(fake_client):
    make_client, _ = fake_client
    errors = (ValidationError("BAD_THING", "something is wrong", {}),)
    client = make_client(error=InvalidSchedulingConfigurationError(_SCHOOL, _YEAR, errors))

    response = client.post(_URL, json={"base_version_number": 1})

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "INVALID_CONFIGURATION"
    assert body["errors"][0]["code"] == "BAD_THING"


def test_regenerate_verification_failure_is_uncaught_like_generate(fake_client):
    """Mirrors `generate_schedule`'s own deliberate omission:
    `ScheduleVerificationFailedError` is an internal defect, never
    caught, left to FastAPI's generic-500 behavior."""
    from school_timetable.application.generate_schedule_service import ScheduleVerificationFailedError

    make_client, _ = fake_client
    client = TestClient(app, raise_server_exceptions=False)
    fake = _FakeGenerateScheduleService(error=ScheduleVerificationFailedError("boom"))
    app.dependency_overrides[get_generate_schedule_service] = lambda: fake
    try:
        response = client.post(_URL, json={"base_version_number": 1})
        assert response.status_code == 500
    finally:
        app.dependency_overrides.pop(get_generate_schedule_service, None)


# == 14: production dependency wiring ========================================


def test_production_wiring_constructs_service_with_configuration_revision_repository():
    from school_timetable.api.dependencies import get_generate_schedule_service as _get
    from school_timetable.persistence.configuration_revision_repository import (
        SqlAlchemyConfigurationRevisionRepository,
    )

    service = _get()
    assert isinstance(service, GenerateScheduleService)
    assert isinstance(service._configuration_revision_repository, SqlAlchemyConfigurationRevisionRepository)


# == 10: REAL PostgreSQL exact-confirmation round trip + stale-confirmation
#        fresh classification ===============================================


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


def _real_client(session_factory) -> TestClient:
    def override_generate_service():
        return GenerateScheduleService(
            SessionFactorySchedulingProblemRepository(session_factory),
            SqlAlchemyScheduleVersionRepository(session_factory),
            SqlAlchemyConfigurationRevisionRepository(session_factory),
        )

    def override_editing_service():
        return ScheduleEditingService(
            SessionFactorySchedulingProblemRepository(session_factory),
            SqlAlchemyScheduleVersionRepository(session_factory),
            SqlAlchemyConfigurationRevisionRepository(session_factory),
        )

    app.dependency_overrides[get_generate_schedule_service] = override_generate_service
    app.dependency_overrides[get_schedule_editing_service] = override_editing_service
    app.dependency_overrides[get_schedule_version_repository] = lambda: SqlAlchemyScheduleVersionRepository(
        session_factory,
    )
    app.dependency_overrides[get_configuration_revision_repository] = (
        lambda: SqlAlchemyConfigurationRevisionRepository(session_factory)
    )
    return TestClient(app)


@pytest.fixture
def real_client(db):
    _session, session_factory = db
    try:
        yield _real_client(session_factory), session_factory
    finally:
        app.dependency_overrides.pop(get_generate_schedule_service, None)
        app.dependency_overrides.pop(get_schedule_editing_service, None)
        app.dependency_overrides.pop(get_schedule_version_repository, None)
        app.dependency_overrides.pop(get_configuration_revision_repository, None)


def _make_teacher_unavailable_at(session_factory, school_id, year_id, teacher_natural_id, day_id, period_id):
    """Directly inserts a `TeacherAvailability(UNAVAILABLE)` row into the
    year's currently open DRAFT revision -- the exact structural change
    `scheduling.lock_compatibility.classify_locks` detects as
    `TEACHER_UNAVAILABLE_AT_SLOT`. Mirrors the direct-ORM-row-insertion
    convention `tests_web/test_schedule_repository.py`'s own real-
    regeneration tests already use for simulating a configuration edit,
    rather than driving every field-editing HTTP route just to make one
    slot unavailable."""
    session = session_factory()
    try:
        year_row = session.execute(
            select(m.AcademicYear)
            .join(m.School, m.School.id == m.AcademicYear.school_id)
            .where(m.School.natural_id == school_id, m.AcademicYear.natural_id == year_id)
        ).scalar_one()
        draft_revision_id = year_row.draft_revision_id
        teacher_id = session.execute(
            select(m.Teacher.id).where(
                m.Teacher.configuration_revision_id == draft_revision_id,
                m.Teacher.natural_id == teacher_natural_id,
            )
        ).scalar_one()
        day_row_id = session.execute(
            select(m.Day.id).where(
                m.Day.configuration_revision_id == draft_revision_id, m.Day.natural_id == day_id,
            )
        ).scalar_one()
        period_row_id = session.execute(
            select(m.Period.id).where(
                m.Period.configuration_revision_id == draft_revision_id, m.Period.natural_id == period_id,
            )
        ).scalar_one()
        max_ordinal = session.execute(
            select(m.TeacherAvailability.ordinal)
            .where(m.TeacherAvailability.configuration_revision_id == draft_revision_id)
            .order_by(m.TeacherAvailability.ordinal.desc())
            .limit(1)
        ).scalar_one_or_none()
        session.add(m.TeacherAvailability(
            academic_year_id=year_row.id, configuration_revision_id=draft_revision_id,
            teacher_id=teacher_id, day_id=day_row_id, period_id=period_row_id,
            status="UNAVAILABLE", ordinal=(max_ordinal or 0) + 1,
        ))
        session.commit()
    finally:
        session.close()


def test_regenerate_exact_confirmation_round_trip_and_fresh_classification_on_change(real_client):
    """The full HTTP lifecycle from Requirement F:

    1. Generate v1, lock `math_8a` at its solved slot, open a draft, and
       make that lock incompatible (teacher unavailable at its slot).
    2. Request 1 (`confirmed_incompatible_lock_keys=[]`) -> 409
       structured classification naming `math_8a`'s lock.
    3. Request 2, echoing back EXACTLY that key -> succeeds (state has
       not changed since Request 1).
    4. A fresh regeneration is now active with zero locks (the
       incompatible one was dropped) -- confirm a THIRD call with a
       stale, non-empty confirmed set (from the now-irrelevant first
       lock) is rejected again, proving stale confirmations are never
       silently accepted.
    """
    client, session_factory = real_client
    problem = build_valid_fixture()
    session = session_factory()
    write_scheduling_problem(session, problem)
    session.commit()
    session.close()

    generate_response = client.post(f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/generate")
    assert generate_response.status_code == 201, generate_response.text
    v1_summary = generate_response.json()

    active_response = client.get(f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/active")
    assert active_response.status_code == 200
    v1 = active_response.json()
    math_entry = next(e for e in v1["entries"] if e["requirement_id"] == "math_8a")

    lock_response = client.post(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/active/lock",
        json={
            "base_version_number": v1["version_number"], "requirement_id": "math_8a",
            "day_id": math_entry["day_id"], "period_id": math_entry["period_id"],
        },
    )
    assert lock_response.status_code == 200, lock_response.text
    v2 = lock_response.json()

    SqlAlchemyConfigurationRevisionRepository(session_factory).begin_draft(
        problem.school.id, problem.academic_year.id,
    )
    _make_teacher_unavailable_at(
        session_factory, problem.school.id, problem.academic_year.id,
        "t_math", math_entry["day_id"], math_entry["period_id"],
    )

    url = f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/active/regenerate"

    # Request 1: no confirmation -> fresh 409 naming math_8a's lock.
    response1 = client.post(url, json={"base_version_number": v2["version_number"]})
    assert response1.status_code == 409, response1.text
    body1 = response1.json()
    assert body1["code"] == "INCOMPATIBLE_LOCKS_REQUIRE_CONFIRMATION"
    assert len(body1["incompatible_locks"]) == 1
    incompatible_key = body1["incompatible_locks"][0]
    assert incompatible_key["requirement_id"] == "math_8a"
    assert incompatible_key["day_id"] == math_entry["day_id"]
    assert incompatible_key["anchor_period_id"] == math_entry["period_id"]

    # Request 2: echo back exactly the key shown -> proceeds.
    response2 = client.post(url, json={
        "base_version_number": v2["version_number"],
        "confirmed_incompatible_lock_keys": [{
            "requirement_id": incompatible_key["requirement_id"],
            "day_id": incompatible_key["day_id"],
            "anchor_period_id": incompatible_key["anchor_period_id"],
        }],
    })
    assert response2.status_code == 200, response2.text
    v3 = response2.json()
    assert v3["version_number"] == v2["version_number"] + 1
    # The incompatible lock was dropped, never persisted.
    assert v3["locked_occurrences"] == []

    # State has moved on (v3 is now active, with zero locks, and the
    # successful regeneration published its draft). Re-open a fresh
    # draft and retry with the FIRST response's now-stale
    # base_version_number/confirmed set -- proving a stale
    # base_version_number is never silently accepted either, even
    # carrying an otherwise well-formed confirmation.
    SqlAlchemyConfigurationRevisionRepository(session_factory).begin_draft(
        problem.school.id, problem.academic_year.id,
    )
    response3 = client.post(url, json={
        "base_version_number": v2["version_number"],
        "confirmed_incompatible_lock_keys": [{
            "requirement_id": incompatible_key["requirement_id"],
            "day_id": incompatible_key["day_id"],
            "anchor_period_id": incompatible_key["anchor_period_id"],
        }],
    })
    assert response3.status_code == 409
    assert response3.json()["code"] == "STALE_SCHEDULE_VERSION"


def test_regenerate_stale_confirmation_returns_fresh_classification_when_compatibility_changes(real_client):
    """Requirement F's other branch: compatibility changes BETWEEN
    Request 1 and Request 2 while the draft stays open and the base
    version stays current -- the caller's Request-1-derived confirmation
    is now stale (it names only `math_8a`'s lock), and a second,
    independent incompatibility (`german_8a`, whose lock/unlock also
    locks its split sibling `russian_8a`) has since appeared. Request 2
    must return a FRESH 409 naming the NEW, larger set -- never silently
    proceed using the stale, now-incomplete confirmation."""
    client, session_factory = real_client
    problem = build_valid_fixture()
    session = session_factory()
    write_scheduling_problem(session, problem)
    session.commit()
    session.close()

    base_url = f"/schools/{problem.school.id}/years/{problem.academic_year.id}"
    generate_response = client.post(f"{base_url}/schedule/generate")
    assert generate_response.status_code == 201, generate_response.text

    active = client.get(f"{base_url}/schedule/active").json()
    math_entry = next(e for e in active["entries"] if e["requirement_id"] == "math_8a")
    german_entry = next(e for e in active["entries"] if e["requirement_id"] == "german_8a")

    lock1 = client.post(f"{base_url}/schedule/active/lock", json={
        "base_version_number": active["version_number"], "requirement_id": "math_8a",
        "day_id": math_entry["day_id"], "period_id": math_entry["period_id"],
    })
    assert lock1.status_code == 200, lock1.text
    v2 = lock1.json()

    lock2 = client.post(f"{base_url}/schedule/active/lock", json={
        "base_version_number": v2["version_number"], "requirement_id": "german_8a",
        "day_id": german_entry["day_id"], "period_id": german_entry["period_id"],
    })
    assert lock2.status_code == 200, lock2.text
    v3 = lock2.json()
    assert {k["requirement_id"] for k in v3["locked_occurrences"]} == {"math_8a", "german_8a", "russian_8a"}

    SqlAlchemyConfigurationRevisionRepository(session_factory).begin_draft(problem.school.id, problem.academic_year.id)
    _make_teacher_unavailable_at(
        session_factory, problem.school.id, problem.academic_year.id,
        "t_math", math_entry["day_id"], math_entry["period_id"],
    )

    url = f"{base_url}/schedule/active/regenerate"
    response1 = client.post(url, json={"base_version_number": v3["version_number"]})
    assert response1.status_code == 409
    body1 = response1.json()
    assert {lock["requirement_id"] for lock in body1["incompatible_locks"]} == {"math_8a"}

    # Compatibility changes again, BEFORE Request 2: german_8a's teacher
    # also becomes unavailable at its locked slot.
    _make_teacher_unavailable_at(
        session_factory, problem.school.id, problem.academic_year.id,
        "t_german", german_entry["day_id"], german_entry["period_id"],
    )

    # Request 2 echoes back exactly Request 1's (now stale) confirmation.
    stale_confirmation = [{
        "requirement_id": lock["requirement_id"], "day_id": lock["day_id"],
        "anchor_period_id": lock["anchor_period_id"],
    } for lock in body1["incompatible_locks"]]
    response2 = client.post(url, json={
        "base_version_number": v3["version_number"],
        "confirmed_incompatible_lock_keys": stale_confirmation,
    })

    assert response2.status_code == 409
    body2 = response2.json()
    assert body2["code"] == "INCOMPATIBLE_LOCKS_REQUIRE_CONFIRMATION"
    fresh_requirement_ids = {lock["requirement_id"] for lock in body2["incompatible_locks"]}
    # The FRESH classification is now larger than the stale confirmation
    # -- german_8a (and its synchronized split sibling russian_8a) newly
    # appear, proving Request 2 was never silently accepted.
    assert fresh_requirement_ids >= {"math_8a", "german_8a"}
    assert fresh_requirement_ids != {"math_8a"}
