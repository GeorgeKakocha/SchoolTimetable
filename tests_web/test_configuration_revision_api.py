"""Real-PostgreSQL HTTP integration tests for Safe Configuration
Changes, Slice B's configuration revision lifecycle routes:
`GET .../configuration/state`, `POST .../configuration/draft`, and
`DELETE .../configuration/draft` (`api/configuration_revision_routes.py`).

Uses the same `join_transaction_mode="create_savepoint"` fixture
pattern as every other `tests_web/test_*_api.py` file: every
adapter-opened `Session` shares the fixture's single outer,
never-committed `Connection`, so nothing here ever leaves a row in the
real database.

This file proves the HTTP/application contract and lifecycle
orchestration ONLY -- the exhaustive 15-table/FK eager-clone
correctness is already proven directly against the real repository in
`tests_web/test_configuration_revision_repository.py` and is
deliberately not re-tested here.
"""
from __future__ import annotations

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from school_timetable.api.dependencies import get_configuration_revision_repository, get_generate_schedule_service
from school_timetable.api.main import app
from school_timetable.application.generate_schedule_service import GenerateScheduleService
from school_timetable.domain.problem import SchedulingProblem
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
    def override_configuration_revision_repository():
        return SqlAlchemyConfigurationRevisionRepository(session_factory)

    def override_generate_service():
        return GenerateScheduleService(
            SessionFactorySchedulingProblemRepository(session_factory),
            SqlAlchemyScheduleVersionRepository(session_factory),
        )

    app.dependency_overrides[get_configuration_revision_repository] = override_configuration_revision_repository
    app.dependency_overrides[get_generate_schedule_service] = override_generate_service
    return TestClient(app)


@pytest.fixture
def client(db):
    _session, session_factory = db
    try:
        yield _client(session_factory)
    finally:
        app.dependency_overrides.pop(get_configuration_revision_repository, None)
        app.dependency_overrides.pop(get_generate_schedule_service, None)


def _seed(session: Session) -> SchedulingProblem:
    problem = build_valid_fixture()
    write_scheduling_problem(session, problem)
    session.flush()
    return problem


def _state_url(problem: SchedulingProblem) -> str:
    return f"/schools/{problem.school.id}/years/{problem.academic_year.id}/configuration/state"


def _draft_url(problem: SchedulingProblem) -> str:
    return f"/schools/{problem.school.id}/years/{problem.academic_year.id}/configuration/draft"


def _generate(client: TestClient, problem: SchedulingProblem):
    response = client.post(f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/generate")
    assert response.status_code == 201
    return response


def _year_row(session: Session, academic_year_natural_id: str) -> m.AcademicYear:
    return session.execute(
        select(m.AcademicYear).where(m.AcademicYear.natural_id == academic_year_natural_id)
    ).scalar_one()


def _revision_count(session: Session, year_id: int) -> int:
    return len(session.execute(
        select(m.ConfigurationRevision).where(m.ConfigurationRevision.academic_year_id == year_id)
    ).scalars().all())


# == A. GET state -- initial/pre-generate state ==============================

def test_get_state_initial_pre_generate(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.get(_state_url(problem))

    assert response.status_code == 200
    assert response.json() == {
        "published_revision_number": None,
        "draft_revision_number": 1,
        "configuration_locked": False,
        "timetable_out_of_date": False,
    }


# == B. GET state -- published/no-draft state =================================

def test_get_state_published_no_draft_after_generate(client, db):
    session, _session_factory = db
    problem = _seed(session)
    _generate(client, problem)

    response = client.get(_state_url(problem))

    assert response.status_code == 200
    assert response.json() == {
        "published_revision_number": 1,
        "draft_revision_number": None,
        "configuration_locked": True,
        "timetable_out_of_date": False,
    }


# == C. POST begin draft ======================================================

def test_post_begin_draft_from_published_state(client, db):
    session, _session_factory = db
    problem = _seed(session)
    _generate(client, problem)
    published_id_before = _year_row(session, problem.academic_year.id).published_revision_id

    response = client.post(_draft_url(problem))

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "published_revision_number": 1,
        "draft_revision_number": 2,
        "configuration_locked": False,
        "timetable_out_of_date": True,
    }

    # GET state immediately reflects the new draft.
    get_response = client.get(_state_url(problem))
    assert get_response.json() == body

    # I: published pointer unchanged (same row, not merely "still set"),
    # draft pointer now set to a new row.
    year_row = _year_row(session, problem.academic_year.id)
    assert year_row.published_revision_id == published_id_before
    assert year_row.draft_revision_id is not None


# == D. POST begin draft idempotency ==========================================

def test_post_begin_draft_idempotent(client, db):
    session, _session_factory = db
    problem = _seed(session)
    _generate(client, problem)

    first = client.post(_draft_url(problem))
    assert first.status_code == 200

    year_id = _year_row(session, problem.academic_year.id).id
    revision_count_before = _revision_count(session, year_id)
    draft_id_before = _year_row(session, problem.academic_year.id).draft_revision_id

    second = client.post(_draft_url(problem))

    assert second.status_code == 200
    assert second.json() == first.json()

    # I: no second revision created; the same draft row is still pointed to.
    assert _revision_count(session, year_id) == revision_count_before
    assert _year_row(session, problem.academic_year.id).draft_revision_id == draft_id_before


# == E. DELETE discard draft ==================================================

def test_delete_discard_draft_after_begin(client, db):
    session, _session_factory = db
    problem = _seed(session)
    _generate(client, problem)
    published_id_before = _year_row(session, problem.academic_year.id).published_revision_id
    begin_response = client.post(_draft_url(problem))
    assert begin_response.status_code == 200

    response = client.delete(_draft_url(problem))

    assert response.status_code == 200
    assert response.json() == {
        "published_revision_number": 1,
        "draft_revision_number": None,
        "configuration_locked": True,
        "timetable_out_of_date": False,
    }

    get_response = client.get(_state_url(problem))
    assert get_response.json() == response.json()

    # I: draft pointer cleared, published pointer is the exact same row
    # it was both before begin_draft and after it.
    year_row = _year_row(session, problem.academic_year.id)
    assert year_row.draft_revision_id is None
    assert year_row.published_revision_id == published_id_before


# == F. DELETE with no draft ===================================================

def test_delete_discard_draft_with_no_open_draft_returns_409(client, db):
    session, _session_factory = db
    problem = _seed(session)
    _generate(client, problem)

    response = client.delete(_draft_url(problem))

    assert response.status_code == 409
    assert response.json() == {
        "code": "NO_CONFIGURATION_DRAFT",
        "detail": (
            f"no configuration draft exists for school={problem.school.id!r}, "
            f"academic_year={problem.academic_year.id!r}"
        ),
    }

    # I: zero mutation on the rejected attempt.
    year_row = _year_row(session, problem.academic_year.id)
    assert year_row.draft_revision_id is None
    assert year_row.published_revision_id is not None


# == G. DELETE initial pre-generate draft ======================================

def test_delete_discard_initial_pre_generate_draft_returns_409(client, db):
    """Constructed only through the legitimate production/repository
    flow: seeding a year (via the same `write_scheduling_problem` every
    other `tests_web` file uses) without ever calling Generate leaves
    it in exactly the state Slice A guarantees -- an initial DRAFT and
    no published revision."""
    session, _session_factory = db
    problem = _seed(session)
    initial_draft_id = _year_row(session, problem.academic_year.id).draft_revision_id
    assert initial_draft_id is not None  # sanity: this really is the initial-draft state

    response = client.delete(_draft_url(problem))

    assert response.status_code == 409
    assert response.json() == {
        "code": "INITIAL_DRAFT_CANNOT_BE_DISCARDED",
        "detail": (
            "the initial pre-first-Generate configuration draft cannot be discarded for "
            f"school={problem.school.id!r}, academic_year={problem.academic_year.id!r}"
        ),
    }

    # I: zero mutation -- the initial draft is completely untouched.
    year_row = _year_row(session, problem.academic_year.id)
    assert year_row.draft_revision_id == initial_draft_id
    assert year_row.published_revision_id is None


# == H. Resource isolation / not-found behavior ================================

def test_get_state_unknown_school_year_returns_404(client):
    response = client.get("/schools/no-such-school/years/no-such-year/configuration/state")
    assert response.status_code == 404
    assert response.json() == {"detail": "Scheduling configuration not found"}


def test_post_begin_draft_unknown_school_year_returns_404(client):
    response = client.post("/schools/no-such-school/years/no-such-year/configuration/draft")
    assert response.status_code == 404
    assert response.json() == {"detail": "Scheduling configuration not found"}


def test_delete_discard_draft_unknown_school_year_returns_404(client):
    response = client.delete("/schools/no-such-school/years/no-such-year/configuration/draft")
    assert response.status_code == 404
    assert response.json() == {"detail": "Scheduling configuration not found"}


def test_get_state_mismatched_school_and_year_returns_404(client, db):
    """A real school and a real academic-year natural ID, but not
    belonging to EACH OTHER, must resolve exactly like an unknown pair
    -- `resolve_year_id` joins on both `School.natural_id` and
    `AcademicYear.school_id` together, never one alone."""
    session, _session_factory = db
    problem_a = _seed(session)
    problem_b = build_valid_fixture()
    problem_b = replace(
        problem_b,
        school=replace(problem_b.school, id="other-school"),
        academic_year=replace(problem_b.academic_year, id="other-year"),
    )
    write_scheduling_problem(session, problem_b)
    session.flush()

    # problem_a's own school + problem_b's own (real, but unrelated) year.
    response = client.get(
        f"/schools/{problem_a.school.id}/years/{problem_b.academic_year.id}/configuration/state"
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Scheduling configuration not found"}
