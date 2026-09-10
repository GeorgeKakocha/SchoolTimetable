"""Real-PostgreSQL HTTP integration tests for Phase 3C.2b's
`GET/POST .../teaching-assignments` and `PUT/DELETE
.../teaching-assignments/{requirement_id}` (`docs/DECISIONS.md` #34-#36's
locked HTTP contract), plus one regression proving the existing
`POST .../schedule/generate` route now maps
`ConfigurationChangedDuringGenerationError` to a retryable 409 instead
of a generic 500.

Uses the same `join_transaction_mode="create_savepoint"` fixture
pattern as `tests_web/test_class_timetable_api.py`/`test_schedule_api.py`:
every adapter-opened `Session` shares the fixture's single outer,
never-committed `Connection`, so nothing here ever leaves a row in the
real database. Genuine multi-connection concurrency proofs (the
duplicate-create race, the generation-vs-write race) already live in
`tests_web/test_teaching_assignment_repository.py` (Phase 3C.2a) --
this file proves the HTTP contract, not the underlying lock mechanism.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from school_timetable.api.dependencies import (
    get_generate_schedule_service,
    get_schedule_version_repository,
    get_teaching_assignment_service,
    get_teaching_assignments_projection_service,
)
from school_timetable.api.main import app
from school_timetable.application.errors import ConfigurationChangedDuringGenerationError
from school_timetable.application.generate_schedule_service import GenerateScheduleService
from school_timetable.application.teaching_assignment_service import TeachingAssignmentService
from school_timetable.application.teaching_assignments_projection_service import (
    TeachingAssignmentsProjectionService,
)
from school_timetable.domain.problem import SchedulingProblem
from school_timetable.fixtures.valid_fixture import build_valid_fixture
from school_timetable.persistence import models as m
from school_timetable.persistence.db import get_session
from school_timetable.persistence.problem_repository import SessionFactorySchedulingProblemRepository
from school_timetable.persistence.schedule_repository import SqlAlchemyScheduleVersionRepository
from school_timetable.persistence.teaching_assignment_repository import SqlAlchemyTeachingAssignmentRepository
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

    def override_projection_service():
        return TeachingAssignmentsProjectionService(
            SessionFactorySchedulingProblemRepository(session_factory),
            SqlAlchemyScheduleVersionRepository(session_factory),
        )

    def override_assignment_service():
        return TeachingAssignmentService(
            SessionFactorySchedulingProblemRepository(session_factory),
            SqlAlchemyTeachingAssignmentRepository(session_factory),
            SqlAlchemyScheduleVersionRepository(session_factory),
        )

    def override_get_session():
        yield session_factory()

    app.dependency_overrides[get_schedule_version_repository] = override_schedule_repo
    app.dependency_overrides[get_generate_schedule_service] = override_generate_service
    app.dependency_overrides[get_teaching_assignments_projection_service] = override_projection_service
    app.dependency_overrides[get_teaching_assignment_service] = override_assignment_service
    app.dependency_overrides[get_session] = override_get_session
    return TestClient(app, raise_server_exceptions=raise_server_exceptions)


@pytest.fixture
def client(db):
    _session, session_factory = db
    try:
        yield _client(session_factory)
    finally:
        _clear_overrides()


def _clear_overrides() -> None:
    app.dependency_overrides.pop(get_schedule_version_repository, None)
    app.dependency_overrides.pop(get_generate_schedule_service, None)
    app.dependency_overrides.pop(get_teaching_assignments_projection_service, None)
    app.dependency_overrides.pop(get_teaching_assignment_service, None)
    app.dependency_overrides.pop(get_session, None)


def _seed(session: Session) -> SchedulingProblem:
    problem = build_valid_fixture()
    write_scheduling_problem(session, problem)
    session.flush()
    return problem


def _url(problem: SchedulingProblem, *parts: str) -> str:
    return "/".join(
        (f"/schools/{problem.school.id}/years/{problem.academic_year.id}/teaching-assignments", *parts)
    )


def _by_id(items: list[dict], item_id: str) -> dict:
    return next(i for i in items if i["id"] == item_id)


# -- GET ------------------------------------------------------------------

def test_get_projection_full_contract_shape(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.get(_url(problem))
    assert response.status_code == 200
    body = response.json()

    assert set(body.keys()) == {
        "configuration_locked", "assignments", "teachers", "whole_class_targets",
        "activities", "teacher_workloads", "resources",
    }
    assert body["configuration_locked"] is False
    assert len(body["assignments"]) == len(problem.teaching_requirements)
    assert {a["id"] for a in body["assignments"]} == {r.id for r in problem.teaching_requirements}

    assignment = body["assignments"][0]
    assert set(assignment.keys()) == {
        "id", "teacher_id", "teacher_name", "activity_id", "activity_name",
        "participant_group_id", "participant_group_name", "participant_group_role",
        "class_sections", "weekly_periods", "editable", "advanced_reasons", "resource_id",
    }


def test_get_editable_and_advanced_reasons_correct(client, db):
    session, _session_factory = db
    problem = _seed(session)

    body = client.get(_url(problem)).json()
    science_8a = _by_id(body["assignments"], "science_8a")
    assert science_8a["editable"] is True
    assert science_8a["advanced_reasons"] == []

    math_8a = _by_id(body["assignments"], "math_8a")
    assert math_8a["editable"] is False
    assert "block_policy" in math_8a["advanced_reasons"]


def test_get_otherwise_plain_resource_bearing_assignment_is_editable(client, db):
    # Resources B1: sport_8a carries resource_requirement=gym but is
    # otherwise plain -- must now be editable, with resource_id exposed
    # and "resource_requirement" never appearing in advanced_reasons.
    session, _session_factory = db
    problem = _seed(session)

    body = client.get(_url(problem)).json()
    sport_8a = _by_id(body["assignments"], "sport_8a")
    assert sport_8a["editable"] is True
    assert sport_8a["advanced_reasons"] == []
    assert sport_8a["resource_id"] == "gym"


def test_get_assignment_without_resource_has_null_resource_id(client, db):
    session, _session_factory = db
    problem = _seed(session)

    body = client.get(_url(problem)).json()
    science_8a = _by_id(body["assignments"], "science_8a")
    assert science_8a["resource_id"] is None


def test_get_other_advanced_reasons_unaffected_by_resources_b1(client, db):
    session, _session_factory = db
    problem = _seed(session)

    body = client.get(_url(problem)).json()
    german_8a = _by_id(body["assignments"], "german_8a")
    assert german_8a["editable"] is False
    assert "split_group_id" in german_8a["advanced_reasons"]
    assert "resource_requirement" not in german_8a["advanced_reasons"]


def test_get_resource_options_included_and_ordered(client, db):
    session, _session_factory = db
    problem = _seed(session)

    body = client.get(_url(problem)).json()
    assert [r["id"] for r in body["resources"]] == [r.id for r in problem.resources]
    gym = _by_id(body["resources"], "gym")
    assert gym["name"] == "Indoor Gym"
    assert gym["capacity"] == 1


def test_get_fixed_placement_makes_assignment_non_editable(client, db):
    session, _session_factory = db
    problem = _seed(session)

    body = client.get(_url(problem)).json()
    art_8b = _by_id(body["assignments"], "art_8b")
    assert art_8b["editable"] is False
    assert art_8b["advanced_reasons"] == ["fixed_placement"]


def test_get_teacher_workload_includes_every_requirement_type(client, db):
    session, _session_factory = db
    problem = _seed(session)

    body = client.get(_url(problem)).json()
    history_workload = next(w for w in body["teacher_workloads"] if w["teacher_id"] == "t_history")
    assert history_workload["total_weekly_periods"] == 9 + 13 + 1


def test_get_zero_workload_teacher_appears_with_zero_total(client, db):
    session, session_factory = db
    problem = _seed(session)

    year_id = session.execute(
        select(m.AcademicYear.id).where(m.AcademicYear.natural_id == problem.academic_year.id)
    ).scalar_one()
    next_ordinal = session.execute(
        select(func.max(m.Teacher.ordinal)).where(m.Teacher.academic_year_id == year_id)
    ).scalar_one()
    session.add(m.Teacher(
        academic_year_id=year_id, natural_id="t_zero",
        first_name="Teacher Zero", last_name="", ordinal=next_ordinal + 1,
    ))
    session.flush()

    body = client.get(_url(problem)).json()
    zero_workload = next(w for w in body["teacher_workloads"] if w["teacher_id"] == "t_zero")
    assert zero_workload["total_weekly_periods"] == 0
    assert zero_workload["teacher_name"] == "Teacher Zero"


def test_get_canonical_whole_class_target_mapping(client, db):
    session, _session_factory = db
    problem = _seed(session)

    body = client.get(_url(problem)).json()
    by_class = {t["class_section_id"]: t for t in body["whole_class_targets"]}
    assert by_class["8a"]["participant_group_id"] == "pg_8a"
    assert by_class["8a"]["participant_group_name"] == "All of 8-A"


def test_get_no_subgroup_or_merged_targets_in_whole_class_targets(client, db):
    session, _session_factory = db
    problem = _seed(session)

    body = client.get(_url(problem)).json()
    target_group_ids = {t["participant_group_id"] for t in body["whole_class_targets"]}
    assert "pg_8a_german" not in target_group_ids
    assert "pg_9a_9b_merged" not in target_group_ids


def test_get_deterministic_ordering(client, db):
    session, _session_factory = db
    problem = _seed(session)

    body = client.get(_url(problem)).json()
    assert [a["id"] for a in body["assignments"]] == [r.id for r in problem.teaching_requirements]
    assert [t["id"] for t in body["teachers"]] == [t.id for t in problem.teachers]
    # Pre-Slice-D correction: activities options are ORDINARY-only.
    ordinary_activity_ids = [a.id for a in problem.activities if a.kind.value == "ORDINARY"]
    assert [a["id"] for a in body["activities"]] == ordinary_activity_ids


def test_get_activity_options_exclude_club_activities(client, db):
    session, _session_factory = db
    problem = _seed(session)

    body = client.get(_url(problem)).json()
    activity_ids = {a["id"] for a in body["activities"]}
    assert "club_chess" not in activity_ids
    assert "club_robotics" not in activity_ids


def test_get_configuration_locked_true_after_generate_and_assignments_still_visible(client, db):
    session, _session_factory = db
    problem = _seed(session)

    generate_response = client.post(f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/generate")
    assert generate_response.status_code == 201

    body = client.get(_url(problem)).json()
    assert body["configuration_locked"] is True
    assert len(body["assignments"]) == len(problem.teaching_requirements)


def test_get_unknown_school_year_returns_config_not_found(client):
    response = client.get("/schools/no-such-school/years/no-such-year/teaching-assignments")
    assert response.status_code == 404
    assert response.json() == {"detail": "Scheduling configuration not found"}


def test_config_still_includes_club_activities_with_kind(client, db):
    # /config is the general scheduling configuration projection and is
    # deliberately NOT filtered by the pre-Slice-D correction -- only
    # the Teaching Assignments editable activity options are.
    session, _session_factory = db
    problem = _seed(session)

    body = client.get(f"/schools/{problem.school.id}/years/{problem.academic_year.id}/config").json()
    activities_by_id = {a["id"]: a for a in body["activities"]}
    assert activities_by_id["club_chess"]["kind"] == "CLUB"
    assert activities_by_id["club_robotics"]["kind"] == "CLUB"
    assert activities_by_id["math"]["kind"] == "ORDINARY"


# -- POST -------------------------------------------------------------------

def test_post_create_201_exact_body(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.post(_url(problem), json={
        "teacher_id": "t_history", "participant_group_id": "pg_9a", "activity_id": "history", "weekly_periods": 3,
    })
    assert response.status_code == 201
    body = response.json()
    assert set(body.keys()) == {"id", "warnings"}
    assert body["id"].startswith("req_")
    assert isinstance(body["warnings"], list)


def test_post_unknown_reference_returns_422(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.post(_url(problem), json={
        "teacher_id": "no-such-teacher", "participant_group_id": "pg_9a", "activity_id": "history", "weekly_periods": 3,
    })
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "UNKNOWN_REFERENCE"
    assert body["reference_kind"] == "teacher"
    assert body["reference_id"] == "no-such-teacher"


def test_post_subgroup_target_returns_422(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.post(_url(problem), json={
        "teacher_id": "t_german", "participant_group_id": "pg_8a_german", "activity_id": "german", "weekly_periods": 3,
    })
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "NON_WHOLE_CLASS_TARGET"
    assert body["actual_role"] == "SUBGROUP"


def test_post_merged_classes_target_returns_422(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.post(_url(problem), json={
        "teacher_id": "t_history", "participant_group_id": "pg_9a_9b_merged", "activity_id": "history", "weekly_periods": 1,
    })
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "NON_WHOLE_CLASS_TARGET"
    assert body["actual_role"] == "MERGED_CLASSES"


def test_post_club_activity_target_returns_422(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.post(_url(problem), json={
        "teacher_id": "t_art", "participant_group_id": "pg_9b", "activity_id": "club_chess", "weekly_periods": 1,
    })
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "NON_ORDINARY_ACTIVITY_TARGET"
    assert body["activity_id"] == "club_chess"
    assert body["actual_kind"] == "CLUB"


def test_post_duplicate_returns_409(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.post(_url(problem), json={
        "teacher_id": "t_math", "participant_group_id": "pg_8a", "activity_id": "math", "weekly_periods": 5,
    })
    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "DUPLICATE_TEACHING_ASSIGNMENT"


def test_post_locked_configuration_returns_409_exact_body(client, db):
    session, _session_factory = db
    problem = _seed(session)

    generate_response = client.post(f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/generate")
    assert generate_response.status_code == 201

    response = client.post(_url(problem), json={
        "teacher_id": "t_history", "participant_group_id": "pg_9a", "activity_id": "history", "weekly_periods": 3,
    })
    assert response.status_code == 409
    assert response.json() == {
        "code": "SCHEDULING_CONFIGURATION_LOCKED",
        "detail": "Scheduling configuration is locked because a schedule already exists",
    }


def test_post_non_positive_weekly_periods_returns_422(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.post(_url(problem), json={
        "teacher_id": "t_history", "participant_group_id": "pg_9a", "activity_id": "history", "weekly_periods": 0,
    })
    assert response.status_code == 422


# -- POST/PUT: Resources B1 (fixed Resource) ---------------------------

def test_post_without_resource_id_creates_with_no_resource(client, db):
    session, session_factory = db
    problem = _seed(session)

    response = client.post(_url(problem), json={
        "teacher_id": "t_history", "participant_group_id": "pg_9a", "activity_id": "history", "weekly_periods": 3,
    })
    assert response.status_code == 201
    new_id = response.json()["id"]

    body = client.get(_url(problem)).json()
    assert _by_id(body["assignments"], new_id)["resource_id"] is None


def test_post_resource_id_null_creates_with_no_resource(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.post(_url(problem), json={
        "teacher_id": "t_history", "participant_group_id": "pg_9a", "activity_id": "history", "weekly_periods": 3,
        "resource_id": None,
    })
    assert response.status_code == 201
    new_id = response.json()["id"]

    body = client.get(_url(problem)).json()
    assert _by_id(body["assignments"], new_id)["resource_id"] is None


def test_post_with_resource_id_creates_with_that_resource(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.post(_url(problem), json={
        "teacher_id": "t_history", "participant_group_id": "pg_9a", "activity_id": "history", "weekly_periods": 3,
        "resource_id": "gym",
    })
    assert response.status_code == 201
    new_id = response.json()["id"]

    body = client.get(_url(problem)).json()
    assert _by_id(body["assignments"], new_id)["resource_id"] == "gym"


def test_post_unknown_resource_id_returns_422(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.post(_url(problem), json={
        "teacher_id": "t_history", "participant_group_id": "pg_9a", "activity_id": "history", "weekly_periods": 3,
        "resource_id": "no-such-resource",
    })
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "UNKNOWN_REFERENCE"
    assert body["reference_kind"] == "resource"
    assert body["reference_id"] == "no-such-resource"


def test_post_cross_ay_resource_id_returns_422(client, db, live_db_engine):
    session, session_factory = db
    problem = _seed(session)

    school2 = m.School(natural_id="other-school-post-cross-ay", name="Other School")
    session.add(school2)
    session.flush()
    year2 = m.AcademicYear(school_id=school2.id, natural_id="other-year", label="Other Year")
    session.add(year2)
    session.flush()
    session.add(m.Resource(academic_year_id=year2.id, natural_id="cross_ay_gym", name="Other Gym", capacity=1, ordinal=0))
    session.flush()

    response = client.post(_url(problem), json={
        "teacher_id": "t_history", "participant_group_id": "pg_9a", "activity_id": "history", "weekly_periods": 3,
        "resource_id": "cross_ay_gym",
    })
    assert response.status_code == 422
    assert response.json()["code"] == "UNKNOWN_REFERENCE"


# -- PUT --------------------------------------------------------------------

def test_put_update_200_preserves_natural_id(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.put(_url(problem, "science_8a"), json={
        "teacher_id": "t_science", "participant_group_id": "pg_8a", "activity_id": "science", "weekly_periods": 7,
    })
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "science_8a"
    assert isinstance(body["warnings"], list)


def test_put_not_found_returns_404(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.put(_url(problem, "does-not-exist"), json={
        "teacher_id": "t_science", "participant_group_id": "pg_8a", "activity_id": "science", "weekly_periods": 7,
    })
    assert response.status_code == 404
    assert response.json() == {"detail": "Teaching assignment not found"}


def test_put_advanced_requirement_returns_409_with_reasons(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.put(_url(problem, "math_8a"), json={
        "teacher_id": "t_math", "participant_group_id": "pg_8a", "activity_id": "math", "weekly_periods": 5,
    })
    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "ADVANCED_REQUIREMENT_NOT_EDITABLE"
    assert "block_policy" in body["advanced_reasons"]


def test_put_duplicate_returns_409(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.put(_url(problem, "science_8a"), json={
        "teacher_id": "t_math", "participant_group_id": "pg_8a", "activity_id": "math", "weekly_periods": 5,
    })
    assert response.status_code == 409
    assert response.json()["code"] == "DUPLICATE_TEACHING_ASSIGNMENT"


def test_put_non_whole_class_target_returns_422(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.put(_url(problem, "science_8a"), json={
        "teacher_id": "t_science", "participant_group_id": "pg_8a_german", "activity_id": "science", "weekly_periods": 7,
    })
    assert response.status_code == 422
    assert response.json()["code"] == "NON_WHOLE_CLASS_TARGET"


def test_put_club_activity_target_returns_422(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.put(_url(problem, "science_8a"), json={
        "teacher_id": "t_science", "participant_group_id": "pg_8a", "activity_id": "club_chess", "weekly_periods": 7,
    })
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "NON_ORDINARY_ACTIVITY_TARGET"
    assert body["activity_id"] == "club_chess"
    assert body["actual_kind"] == "CLUB"


def test_put_locked_configuration_returns_409(client, db):
    session, _session_factory = db
    problem = _seed(session)

    generate_response = client.post(f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/generate")
    assert generate_response.status_code == 201

    response = client.put(_url(problem, "science_8a"), json={
        "teacher_id": "t_science", "participant_group_id": "pg_8a", "activity_id": "science", "weekly_periods": 9,
    })
    assert response.status_code == 409
    assert response.json()["code"] == "SCHEDULING_CONFIGURATION_LOCKED"


def test_put_without_resource_id_clears_existing_resource(client, db):
    # sport_8a starts with resource_requirement=gym.
    session, _session_factory = db
    problem = _seed(session)

    response = client.put(_url(problem, "sport_8a"), json={
        "teacher_id": "t_sport", "participant_group_id": "pg_8a", "activity_id": "sport", "weekly_periods": 2,
    })
    assert response.status_code == 200

    body = client.get(_url(problem)).json()
    assert _by_id(body["assignments"], "sport_8a")["resource_id"] is None


def test_put_resource_id_null_clears_existing_resource(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.put(_url(problem, "sport_8a"), json={
        "teacher_id": "t_sport", "participant_group_id": "pg_8a", "activity_id": "sport", "weekly_periods": 2,
        "resource_id": None,
    })
    assert response.status_code == 200

    body = client.get(_url(problem)).json()
    assert _by_id(body["assignments"], "sport_8a")["resource_id"] is None


def test_put_replaces_resource_a_with_resource_b(client, db, live_db_engine):
    session, session_factory = db
    problem = _seed(session)
    year_id = session.execute(
        select(m.AcademicYear.id).where(m.AcademicYear.natural_id == problem.academic_year.id)
    ).scalar_one()
    session.add(m.Resource(academic_year_id=year_id, natural_id="lab", name="Science Lab", capacity=1, ordinal=999))
    session.flush()

    response = client.put(_url(problem, "sport_8a"), json={
        "teacher_id": "t_sport", "participant_group_id": "pg_8a", "activity_id": "sport", "weekly_periods": 2,
        "resource_id": "lab",
    })
    assert response.status_code == 200

    body = client.get(_url(problem)).json()
    assert _by_id(body["assignments"], "sport_8a")["resource_id"] == "lab"


def test_put_assigns_resource_to_previously_unassigned_row(client, db):
    # science_8a starts with no resource_requirement.
    session, _session_factory = db
    problem = _seed(session)

    response = client.put(_url(problem, "science_8a"), json={
        "teacher_id": "t_science", "participant_group_id": "pg_8a", "activity_id": "science", "weekly_periods": 9,
        "resource_id": "gym",
    })
    assert response.status_code == 200

    body = client.get(_url(problem)).json()
    assert _by_id(body["assignments"], "science_8a")["resource_id"] == "gym"


def test_put_unknown_resource_id_returns_422(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.put(_url(problem, "science_8a"), json={
        "teacher_id": "t_science", "participant_group_id": "pg_8a", "activity_id": "science", "weekly_periods": 9,
        "resource_id": "no-such-resource",
    })
    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "UNKNOWN_REFERENCE"
    assert body["reference_kind"] == "resource"


def test_put_cross_ay_resource_id_returns_422(client, db, live_db_engine):
    session, session_factory = db
    problem = _seed(session)

    school2 = m.School(natural_id="other-school-put-cross-ay", name="Other School")
    session.add(school2)
    session.flush()
    year2 = m.AcademicYear(school_id=school2.id, natural_id="other-year", label="Other Year")
    session.add(year2)
    session.flush()
    session.add(m.Resource(academic_year_id=year2.id, natural_id="cross_ay_gym", name="Other Gym", capacity=1, ordinal=0))
    session.flush()

    response = client.put(_url(problem, "science_8a"), json={
        "teacher_id": "t_science", "participant_group_id": "pg_8a", "activity_id": "science", "weekly_periods": 9,
        "resource_id": "cross_ay_gym",
    })
    assert response.status_code == 422
    assert response.json()["code"] == "UNKNOWN_REFERENCE"


def test_put_otherwise_plain_resource_bearing_requirement_editable(client, db):
    # Resources B1 closure regression: sport_8a must now be a normal
    # PUT target (was previously ADVANCED_REQUIREMENT_NOT_EDITABLE).
    session, _session_factory = db
    problem = _seed(session)

    response = client.put(_url(problem, "sport_8a"), json={
        "teacher_id": "t_sport", "participant_group_id": "pg_8a", "activity_id": "sport", "weekly_periods": 4,
    })
    assert response.status_code == 200
    assert response.json()["id"] == "sport_8a"


# -- DELETE -------------------------------------------------------------

def test_delete_200_exact_body(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.delete(_url(problem, "science_8a"))
    assert response.status_code == 200
    body = response.json()
    assert body["deleted_id"] == "science_8a"
    assert isinstance(body["warnings"], list)


def test_delete_not_found_returns_404(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.delete(_url(problem, "does-not-exist"))
    assert response.status_code == 404
    assert response.json() == {"detail": "Teaching assignment not found"}


def test_delete_advanced_requirement_returns_409(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.delete(_url(problem, "math_8a"))
    assert response.status_code == 409
    body = response.json()
    assert body["code"] == "ADVANCED_REQUIREMENT_NOT_EDITABLE"
    assert "block_policy" in body["advanced_reasons"]


def test_delete_otherwise_plain_resource_bearing_requirement_succeeds(client, db):
    session, _session_factory = db
    problem = _seed(session)

    response = client.delete(_url(problem, "sport_8a"))
    assert response.status_code == 200
    assert response.json()["deleted_id"] == "sport_8a"


def test_delete_locked_configuration_returns_409(client, db):
    session, _session_factory = db
    problem = _seed(session)

    generate_response = client.post(f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/generate")
    assert generate_response.status_code == 201

    response = client.delete(_url(problem, "science_8a"))
    assert response.status_code == 409
    assert response.json()["code"] == "SCHEDULING_CONFIGURATION_LOCKED"


# -- GENERATE regression: ConfigurationChangedDuringGenerationError ---------

def test_generate_configuration_changed_during_generation_maps_to_409_not_500(client, db, monkeypatch):
    session, _session_factory = db
    problem = _seed(session)

    def _raise_config_changed(self, *args, **kwargs):
        raise ConfigurationChangedDuringGenerationError(problem.school.id, problem.academic_year.id)

    monkeypatch.setattr(SqlAlchemyScheduleVersionRepository, "persist_initial_version", _raise_config_changed)

    response = client.post(f"/schools/{problem.school.id}/years/{problem.academic_year.id}/schedule/generate")
    assert response.status_code == 409
    assert response.json() == {
        "code": "CONFIGURATION_CHANGED_DURING_GENERATION",
        "detail": "Scheduling configuration changed during generation; retry generation",
    }
