"""Real-PostgreSQL HTTP integration tests for Real-School Setup MVP
Slice C's `GET/POST .../classes` and `PUT/DELETE .../classes/{class_id}`.

Uses the same `join_transaction_mode="create_savepoint"` fixture
pattern as `tests_web/test_teacher_api.py`: every adapter-opened
`Session` shares the fixture's single outer, never-committed
`Connection`, so nothing here ever leaves a row in the real database.
The genuine multi-connection concurrency proof (the generation-vs-write
race) lives in `tests_web/test_class_section_repository.py` -- this
file proves the HTTP contract, not the underlying lock mechanism.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from school_timetable.api.dependencies import (
    get_class_section_service,
    get_classes_projection_service,
    get_generate_schedule_service,
    get_schedule_version_repository,
    get_teaching_assignments_projection_service,
)
from school_timetable.api.main import app
from school_timetable.application.class_section_projection_service import ClassSectionProjectionService
from school_timetable.application.class_section_service import ClassSectionService
from school_timetable.application.generate_schedule_service import GenerateScheduleService
from school_timetable.application.teaching_assignments_projection_service import (
    TeachingAssignmentsProjectionService,
)
from school_timetable.domain.problem import SchedulingProblem
from school_timetable.fixtures.valid_fixture import build_valid_fixture
from school_timetable.persistence import models as m
from school_timetable.persistence.class_section_repository import SqlAlchemyClassSectionRepository
from school_timetable.persistence.configuration_revision_repository import (
    SqlAlchemyConfigurationRevisionRepository,
)
from school_timetable.persistence.db import get_session
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
    def override_get_session():
        yield session_factory()

    def override_schedule_repo():
        return SqlAlchemyScheduleVersionRepository(session_factory)

    def override_generate_service():
        return GenerateScheduleService(
            SessionFactorySchedulingProblemRepository(session_factory),
            SqlAlchemyScheduleVersionRepository(session_factory),
        )

    def override_classes_projection_service():
        return ClassSectionProjectionService(
            SessionFactorySchedulingProblemRepository(session_factory),
            SqlAlchemyConfigurationRevisionRepository(session_factory),
        )

    def override_teaching_assignments_projection_service():
        return TeachingAssignmentsProjectionService(
            SessionFactorySchedulingProblemRepository(session_factory),
            SqlAlchemyConfigurationRevisionRepository(session_factory),
        )

    def override_class_section_service():
        return ClassSectionService(
            SessionFactorySchedulingProblemRepository(session_factory),
            SqlAlchemyClassSectionRepository(session_factory),
        )

    app.dependency_overrides[get_session] = override_get_session
    app.dependency_overrides[get_schedule_version_repository] = override_schedule_repo
    app.dependency_overrides[get_generate_schedule_service] = override_generate_service
    app.dependency_overrides[get_classes_projection_service] = override_classes_projection_service
    app.dependency_overrides[get_teaching_assignments_projection_service] = (
        override_teaching_assignments_projection_service
    )
    app.dependency_overrides[get_class_section_service] = override_class_section_service
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
    app.dependency_overrides.pop(get_classes_projection_service, None)
    app.dependency_overrides.pop(get_teaching_assignments_projection_service, None)
    app.dependency_overrides.pop(get_class_section_service, None)


def _seed(session: Session) -> SchedulingProblem:
    problem = build_valid_fixture()
    write_scheduling_problem(session, problem)
    session.flush()
    return problem


def _url(problem: SchedulingProblem, *parts: str) -> str:
    return "/".join((f"/schools/{problem.school.id}/years/{problem.academic_year.id}/classes", *parts))


def _by_id(items: list[dict], item_id: str) -> dict:
    return next(i for i in items if i["id"] == item_id)


# -- GET --------------------------------------------------------------------

def test_get_exact_response_shape(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.get(_url(problem))
    assert response.status_code == 200
    body = response.json()
    assert set(body.keys()) == {"configuration_locked", "classes"}
    assert body["configuration_locked"] is False
    assert len(body["classes"]) == len(problem.class_sections)
    item = body["classes"][0]
    assert set(item.keys()) == {"id", "name"}


def test_get_ordinal_ordering_deterministic(client, db):
    session, _session_factory = db
    problem = _seed(session)

    body = client.get(_url(problem)).json()
    assert [c["id"] for c in body["classes"]] == [c.id for c in problem.class_sections]


def test_get_configuration_locked_true_after_generate(client, db):
    session, _session_factory = db
    problem = _seed(session)

    generate_response = client.post(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/generate"
    )
    assert generate_response.status_code == 201

    body = client.get(_url(problem)).json()
    assert body["configuration_locked"] is True
    assert len(body["classes"]) == len(problem.class_sections)


def test_get_unknown_school_year_returns_404(client):
    response = client.get("/schools/no-such-school/years/no-such-year/classes")
    assert response.status_code == 404
    assert response.json() == {"detail": "Scheduling configuration not found"}


# -- POST -------------------------------------------------------------------

def test_post_create_201_exact_body(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.post(_url(problem), json={"name": "10-A"})
    assert response.status_code == 201
    body = response.json()
    assert set(body.keys()) == {"id", "name"}
    assert body["id"].startswith("class_")
    assert body["name"] == "10-A"


def test_post_generated_id_never_accepted_from_body(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.post(_url(problem), json={"name": "10-A", "id": "class_client_supplied"})
    assert response.status_code == 201
    assert response.json()["id"] != "class_client_supplied"


def test_post_stores_trimmed_name(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.post(_url(problem), json={"name": "  10-A  "})
    assert response.status_code == 201
    assert response.json()["name"] == "10-A"


def test_post_creates_canonical_whole_class_group_and_membership(client, db, live_db_engine):
    session, session_factory = db
    problem = _seed(session)

    response = client.post(_url(problem), json={"name": "10-A"})
    assert response.status_code == 201
    class_natural_id = response.json()["id"]

    check_session = session_factory()
    year_id = check_session.execute(
        select(m.AcademicYear.id).join(m.School, m.School.id == m.AcademicYear.school_id).where(
            m.School.natural_id == problem.school.id, m.AcademicYear.natural_id == problem.academic_year.id,
        )
    ).scalar_one()
    class_row = check_session.execute(
        select(m.ClassSection).where(
            m.ClassSection.academic_year_id == year_id, m.ClassSection.natural_id == class_natural_id,
        )
    ).scalar_one()

    memberships = check_session.execute(
        select(m.ParticipantGroupClassSection).where(
            m.ParticipantGroupClassSection.academic_year_id == year_id,
            m.ParticipantGroupClassSection.class_section_id == class_row.id,
        )
    ).scalars().all()
    assert len(memberships) == 1
    assert memberships[0].ordinal == 0

    group_row = check_session.execute(
        select(m.ParticipantGroup).where(
            m.ParticipantGroup.academic_year_id == year_id,
            m.ParticipantGroup.id == memberships[0].participant_group_id,
        )
    ).scalar_one()
    assert group_row.role == "WHOLE_CLASS"
    assert group_row.name == "10-A"
    assert group_row.academic_year_id == class_row.academic_year_id
    check_session.close()


def test_post_duplicate_exact_trimmed_name_returns_409(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.post(_url(problem), json={"name": "8-A"})
    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "DUPLICATE_CLASS"


def test_post_case_sensitive_distinct_name_coexists(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.post(_url(problem), json={"name": "8-a"})
    assert response.status_code == 201


def test_post_locked_configuration_returns_409_exact_body(client, db):
    session, _session_factory = db
    problem = _seed(session)

    generate_response = client.post(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/generate"
    )
    assert generate_response.status_code == 201

    response = client.post(_url(problem), json={"name": "10-A"})
    assert response.status_code == 409
    assert response.json() == {
        "code": "SCHEDULING_CONFIGURATION_LOCKED",
        "detail": "Scheduling configuration is locked because a schedule already exists",
    }


def test_post_blank_name_returns_422(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.post(_url(problem), json={"name": "   "})
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "INVALID_CLASS"
    assert any(e["code"] == "BLANK_CLASS_NAME" for e in body["errors"])


# -- PUT --------------------------------------------------------------------

def test_put_update_200_preserves_class_id(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.put(_url(problem, "8a"), json={"name": "8-Z"})
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "8a"
    assert body["name"] == "8-Z"


def test_put_not_found_returns_404(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.put(_url(problem, "does-not-exist"), json={"name": "X"})
    assert response.status_code == 404
    assert response.json() == {"detail": "Class section not found"}


def test_put_duplicate_target_name_returns_409(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.put(_url(problem, "8a"), json={"name": "8-B"})
    assert response.status_code == 409
    assert response.json()["code"] == "DUPLICATE_CLASS"


def test_put_locked_configuration_returns_409(client, db):
    session, _session_factory = db
    problem = _seed(session)

    generate_response = client.post(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/generate"
    )
    assert generate_response.status_code == 201

    response = client.put(_url(problem, "8a"), json={"name": "8-Z"})
    assert response.status_code == 409
    assert response.json()["code"] == "SCHEDULING_CONFIGURATION_LOCKED"


def test_put_rename_visible_immediately_in_classes_config_and_assignments(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.put(_url(problem, "8a"), json={"name": "8-Z"})
    assert response.status_code == 200

    classes_body = client.get(_url(problem)).json()
    assert _by_id(classes_body["classes"], "8a")["name"] == "8-Z"

    config_body = client.get(f"/schools/{problem.school.id}/years/{problem.academic_year.id}/config").json()
    assert _by_id(config_body["class_sections"], "8a")["name"] == "8-Z"
    whole_class_group = next(
        g for g in config_body["participant_groups"]
        if g["role"] == "WHOLE_CLASS" and g["class_sections"] == ["8a"]
    )
    assert whole_class_group["name"] == "8-Z"

    assignments_body = client.get(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/teaching-assignments"
    ).json()
    target = next(t for t in assignments_body["whole_class_targets"] if t["class_section_id"] == "8a")
    assert target["class_section_name"] == "8-Z"
    assert target["participant_group_name"] == "8-Z"
    assert target["participant_group_id"] == whole_class_group["id"]


# -- DELETE -------------------------------------------------------------

def test_delete_unused_class_200_exact_body(client, db):
    session, _session_factory = db
    problem = _seed(session)

    create_response = client.post(_url(problem), json={"name": "10-A"})
    new_id = create_response.json()["id"]

    response = client.delete(_url(problem, new_id))
    assert response.status_code == 200
    assert response.json() == {"deleted_id": new_id}

    body = client.get(_url(problem)).json()
    assert new_id not in {c["id"] for c in body["classes"]}


def test_delete_removes_owned_canonical_group_and_membership(client, db):
    session, session_factory = db
    problem = _seed(session)

    create_response = client.post(_url(problem), json={"name": "10-A"})
    new_id = create_response.json()["id"]
    client.delete(_url(problem, new_id))

    check_session = session_factory()
    year_id = check_session.execute(
        select(m.AcademicYear.id).join(m.School, m.School.id == m.AcademicYear.school_id).where(
            m.School.natural_id == problem.school.id, m.AcademicYear.natural_id == problem.academic_year.id,
        )
    ).scalar_one()
    remaining_group = check_session.execute(
        select(m.ParticipantGroup).where(
            m.ParticipantGroup.academic_year_id == year_id, m.ParticipantGroup.name == "10-A",
        )
    ).scalar_one_or_none()
    assert remaining_group is None
    check_session.close()


def test_delete_not_found_returns_404(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.delete(_url(problem, "does-not-exist"))
    assert response.status_code == 404
    assert response.json() == {"detail": "Class section not found"}


def test_delete_teaching_requirement_reference_returns_409(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.delete(_url(problem, "9a"))
    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "CLASS_IN_USE"
    assert "TEACHING_REQUIREMENT" in body["referenced_by"]

    get_body = client.get(_url(problem)).json()
    assert "9a" in {c["id"] for c in get_body["classes"]}


def test_delete_still_rejected_once_locked_even_when_also_in_use(client, db):
    """Safe Configuration Changes, Slice B removed the service's own
    fast, un-locked, lock-only precheck (`ClassSectionService` no
    longer has a `ScheduleVersionRepository` dependency at all) -- the
    repository's authoritative lock check is the sole source of truth
    for locking, but it is reached only AFTER the service's own
    validate-precheck, which for delete checks "is this class
    referenced" first. A genuinely UNREFERENCED class cannot exist in a
    locked (post-Generate) configuration in the first place: preflight
    requires every class's declared periods to exactly match its
    instructional slots (`CLASS_OCCUPANCY_MISMATCH`), so a successfully
    generated schedule's classes are always fully referenced. Faking an
    unreferenced class by inserting directly into the published,
    immutable revision would fabricate a state that can never
    legitimately occur -- so this test instead proves the real,
    reachable safety property: deleting a class that is both in-use AND
    the configuration is locked is rejected either way, and the more
    specific, more informative error (`CLASS_IN_USE`) correctly takes
    precedence -- never a silent deletion, never the wrong outcome, and
    the pure lock path itself is separately proven by
    `test_put_locked_configuration_returns_409` (rename has no in-use
    check, so it reaches the repository's lock rejection directly)."""
    session, _session_factory = db
    problem = _seed(session)

    generate_response = client.post(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/generate"
    )
    assert generate_response.status_code == 201

    response = client.delete(_url(problem, "8a"))
    assert response.status_code == 409
    assert response.json()["code"] == "CLASS_IN_USE"

    # The safety property that actually matters: the class was NOT
    # deleted -- configuration remains completely unchanged once locked.
    get_body = client.get(_url(problem)).json()
    assert "8a" in {c["id"] for c in get_body["classes"]}


# -- INTEROPERABILITY: cross-flow visibility ---------------------------------

def test_create_visible_across_config_and_teaching_assignments(client, db):
    session, _session_factory = db
    problem = _seed(session)

    create_response = client.post(_url(problem), json={"name": "10-A"})
    assert create_response.status_code == 201
    new_id = create_response.json()["id"]

    classes_body = client.get(_url(problem)).json()
    created = _by_id(classes_body["classes"], new_id)
    assert created["name"] == "10-A"

    config_body = client.get(f"/schools/{problem.school.id}/years/{problem.academic_year.id}/config").json()
    assert _by_id(config_body["class_sections"], new_id)["name"] == "10-A"
    whole_class_group = next(
        g for g in config_body["participant_groups"]
        if g["role"] == "WHOLE_CLASS" and g["class_sections"] == [new_id]
    )
    assert whole_class_group["name"] == "10-A"

    assignments_body = client.get(
        f"/schools/{problem.school.id}/years/{problem.academic_year.id}/teaching-assignments"
    ).json()
    target = next(t for t in assignments_body["whole_class_targets"] if t["class_section_id"] == new_id)
    assert target["class_section_name"] == "10-A"
    assert target["participant_group_id"] == whole_class_group["id"]
