from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from school_timetable.api.dependencies import get_synchronized_split_service
from school_timetable.api.main import app
from school_timetable.application.errors import (
    ClassSectionNotFoundError,
    ConfigurationLockedError,
    DuplicateSubgroupNameError,
    InvalidSynchronizedSplitError,
    NonOrdinaryActivityTargetError,
    PublicIdCollisionError,
    SameTeacherSynchronizedSplitError,
    SchedulingProblemNotFoundError,
    UnknownReferenceError,
)
from school_timetable.application.synchronized_split_models import (
    SynchronizedSplitBranchResult,
    SynchronizedSplitResult,
)
from school_timetable.application.synchronized_split_service import SynchronizedSplitService
from school_timetable.fixtures.valid_fixture import build_valid_fixture
from school_timetable.persistence import models as m
from school_timetable.persistence.db import get_session
from school_timetable.persistence.problem_repository import SessionFactorySchedulingProblemRepository
from school_timetable.persistence.synchronized_split_repository import SqlAlchemySynchronizedSplitRepository
from school_timetable.validation.errors import ValidationError
from tests_web.support.problem_writer import write_scheduling_problem


@pytest.fixture
def db(live_db_engine):
    connection = live_db_engine.connect()
    transaction = connection.begin()
    factory = sessionmaker(
        bind=connection, autoflush=False, autocommit=False,
        expire_on_commit=False, join_transaction_mode="create_savepoint",
    )
    session = factory()
    try:
        yield session, factory
    finally:
        session.close()
        transaction.rollback()
        connection.close()


def _url(school="synthetic-school", year="ay-2026"):
    return f"/schools/{school}/years/{year}/configuration/synchronized-splits"


def _body():
    return {
        "class_section_id": "9a",
        "weekly_periods": 2,
        "branches": [
            {"participant_group_name": "Russian subgroup", "teacher_id": "t_russian", "activity_id": "russian"},
            {"participant_group_name": "German subgroup", "teacher_id": "t_german", "activity_id": "german"},
        ],
    }


class _FakeService:
    def __init__(self, outcome=None):
        self.outcome = outcome or SynchronizedSplitResult(
            "split_server", "9a", 2,
            SynchronizedSplitBranchResult(
                "group_server_a", "Russian subgroup", "t_russian", "russian", "req_server_a",
            ),
            SynchronizedSplitBranchResult("group_server_b", "German subgroup", "t_german", "german", "req_server_b"),
        )
        self.calls = []

    def create(self, command):
        self.calls.append(command)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def _fake_client(service):
    app.dependency_overrides[get_synchronized_split_service] = lambda: service
    return TestClient(app)


def _clear_overrides():
    app.dependency_overrides.pop(get_synchronized_split_service, None)
    app.dependency_overrides.pop(get_session, None)


def test_success_returns_201_complete_public_aggregate_and_calls_service_once():
    service = _FakeService()
    try:
        response = _fake_client(service).post(_url(), json=_body())
    finally:
        _clear_overrides()
    assert response.status_code == 201
    assert len(service.calls) == 1
    command = service.calls[0]
    assert command.school_id == "synthetic-school" and command.academic_year_id == "ay-2026"
    assert command.class_section_id == "9a" and command.weekly_periods == 2
    body = response.json()
    assert body["split_group_id"] == "split_server"
    assert [branch["participant_group_id"] for branch in body["branches"]] == ["group_server_a", "group_server_b"]
    assert [branch["requirement_id"] for branch in body["branches"]] == ["req_server_a", "req_server_b"]
    assert "id" not in body and all("id" not in branch for branch in body["branches"])


@pytest.mark.parametrize("count", [0, 1, 3])
def test_http_boundary_rejects_any_branch_count_other_than_two(count):
    service = _FakeService(); body = _body(); body["branches"] = body["branches"][:1] * count
    try:
        response = _fake_client(service).post(_url(), json=body)
    finally:
        _clear_overrides()
    assert response.status_code == 422
    assert service.calls == []


def test_http_boundary_accepts_exactly_two_branches():
    service = _FakeService()
    try:
        response = _fake_client(service).post(_url(), json=_body())
    finally:
        _clear_overrides()
    assert response.status_code == 201 and len(service.calls) == 1


def test_http_boundary_rejects_nonpositive_weekly_periods():
    service = _FakeService(); body = _body(); body["weekly_periods"] = 0
    try:
        response = _fake_client(service).post(_url(), json=body)
    finally:
        _clear_overrides()
    assert response.status_code == 422 and service.calls == []


@pytest.mark.parametrize(("error", "status", "code"), [
    (DuplicateSubgroupNameError("Same"), 422, "DUPLICATE_SUBGROUP_NAMES"),
    (SameTeacherSynchronizedSplitError("t_same"), 422, "SAME_TEACHER_SYNCHRONIZED_SPLIT"),
    (InvalidSynchronizedSplitError((ValidationError("BLANK_SUBGROUP_NAME", "blank"),)),
     422, "INVALID_SYNCHRONIZED_SPLIT"),
    (InvalidSynchronizedSplitError((ValidationError("SPLIT_GROUP_WEEKLY_MISMATCH", "invalid"),)),
     422, "INVALID_SYNCHRONIZED_SPLIT"),
    (PublicIdCollisionError("group_collision"), 409, "SYNCHRONIZED_SPLIT_PUBLIC_ID_COLLISION"),
    (ConfigurationLockedError("synthetic-school", "ay-2026"), 409, "SCHEDULING_CONFIGURATION_LOCKED"),
    (UnknownReferenceError("synthetic-school", "ay-2026", "teacher", "missing"),
     422, "UNKNOWN_REFERENCE"),
    (UnknownReferenceError("synthetic-school", "ay-2026", "activity", "missing"),
     422, "UNKNOWN_REFERENCE"),
    (NonOrdinaryActivityTargetError("synthetic-school", "ay-2026", "club", "CLUB"),
     422, "NON_ORDINARY_ACTIVITY_TARGET"),
])
def test_stable_write_error_mapping(error, status, code):
    try:
        response = _fake_client(_FakeService(error)).post(_url(), json=_body())
    finally:
        _clear_overrides()
    assert response.status_code == status
    assert response.json()["code"] == code


@pytest.mark.parametrize(("error", "detail"), [
    (SchedulingProblemNotFoundError("missing", "missing"), "Scheduling configuration not found"),
    (ClassSectionNotFoundError("synthetic-school", "ay-2026", "missing"), "Class section not found"),
])
def test_not_found_error_mapping(error, detail):
    try:
        response = _fake_client(_FakeService(error)).post(_url(), json=_body())
    finally:
        _clear_overrides()
    assert response.status_code == 404 and response.json() == {"detail": detail}


def _real_client(factory):
    generated_ids = iter(("split_api", "group_api_a", "group_api_b", "req_api_a", "req_api_b"))
    app.dependency_overrides[get_synchronized_split_service] = lambda: SynchronizedSplitService(
        SessionFactorySchedulingProblemRepository(factory),
        SqlAlchemySynchronizedSplitRepository(factory),
        id_factory=lambda _prefix: next(generated_ids),
    )
    app.dependency_overrides[get_session] = lambda: factory()
    return TestClient(app)


def test_postgresql_api_atomic_persistence_and_config_readback(db):
    session, factory = db; problem = build_valid_fixture(); write_scheduling_problem(session, problem); session.flush()
    try:
        client = _real_client(factory)
        response = client.post(_url(problem.school.id, problem.academic_year.id), json=_body())
        config = client.get(f"/schools/{problem.school.id}/years/{problem.academic_year.id}/config")
    finally:
        _clear_overrides()
    assert response.status_code == 201 and config.status_code == 200
    payload = response.json(); config_body = config.json()
    assert payload["warnings"]
    assert set(payload["warnings"][0]) == {"code", "message", "context"}
    split_id = payload["split_group_id"]
    groups = [g for g in config_body["participant_groups"] if g["id"] in {"group_api_a", "group_api_b"}]
    requirements = [r for r in config_body["teaching_requirements"] if r["split_group_id"] == split_id]
    assert len(groups) == 2 and all(g["role"] == "SUBGROUP" and g["class_sections"] == ["9a"] for g in groups)
    assert len(requirements) == 2
    assert {r["participant_group_id"] for r in requirements} == {"group_api_a", "group_api_b"}
    assert {r["weekly_periods"] for r in requirements} == {2}
    with factory() as check:
        year_id = check.scalar(select(m.AcademicYear.id).where(m.AcademicYear.natural_id == problem.academic_year.id))
        assert check.scalar(select(func.count()).select_from(m.ParticipantGroup).where(
            m.ParticipantGroup.academic_year_id == year_id,
            m.ParticipantGroup.natural_id.in_(["group_api_a", "group_api_b"]),
        )) == 2
        assert check.scalar(select(func.count()).select_from(m.ParticipantGroupClassSection).join(
            m.ParticipantGroup, m.ParticipantGroup.id == m.ParticipantGroupClassSection.participant_group_id,
        ).where(m.ParticipantGroup.natural_id.in_(["group_api_a", "group_api_b"]))) == 2
        assert check.scalar(select(func.count()).select_from(m.TeachingRequirement).where(
            m.TeachingRequirement.academic_year_id == year_id,
            m.TeachingRequirement.split_group_id == split_id,
        )) == 2


def test_failed_api_request_leaves_no_partial_aggregate(db):
    session, factory = db; problem = build_valid_fixture(); write_scheduling_problem(session, problem); session.flush()
    body = _body(); body["branches"][1]["teacher_id"] = "t_russian"
    try:
        response = _real_client(factory).post(_url(problem.school.id, problem.academic_year.id), json=body)
    finally:
        _clear_overrides()
    assert response.status_code == 422
    with factory() as check:
        assert check.scalar(select(func.count()).select_from(m.ParticipantGroup).where(
            m.ParticipantGroup.natural_id.in_(["group_api_a", "group_api_b"]))) == 0
        assert check.scalar(select(func.count()).select_from(m.TeachingRequirement).where(
            m.TeachingRequirement.natural_id.in_(["req_api_a", "req_api_b"]))) == 0
