"""PostgreSQL/API proof for atomic School/initial-year provisioning."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from school_timetable.api.dependencies import (
    get_configuration_revision_repository,
    get_schedule_version_repository,
    get_school_provisioning_service,
    get_teacher_service,
)
from school_timetable.api.main import app
from school_timetable.application.errors import InvalidSchoolProvisioningError, SchoolProvisioningConflictError
from school_timetable.application.school_provisioning_models import ProvisionSchoolWithInitialYearCommand
from school_timetable.application.school_provisioning_service import ProvisionSchoolWithInitialYearService
from school_timetable.persistence import models as m
from school_timetable.persistence.configuration_revision_repository import (
    SqlAlchemyConfigurationRevisionRepository,
)
from school_timetable.persistence.db import get_session
from school_timetable.persistence.problem_repository import (
    SessionFactorySchedulingProblemRepository,
)
from school_timetable.persistence.schedule_repository import SqlAlchemyScheduleVersionRepository
from school_timetable.persistence.school_provisioning_repository import SqlAlchemySchoolProvisioningRepository
from school_timetable.persistence.teacher_repository import SqlAlchemyTeacherRepository
from school_timetable.application.teacher_service import TeacherService
from school_timetable.validation.errors import ValidationError


def _suffix() -> str:
    return uuid4().hex


def _payload(suffix: str | None = None, **overrides) -> dict:
    suffix = suffix or _suffix()
    value = {
        "school_id": f"provision-school-{suffix}",
        "school_name": "Provisioned School",
        "initial_academic_year": {
            "academic_year_id": f"ay-{suffix}",
            "label": "2026/2027",
        },
    }
    value.update(overrides)
    return value


def _command(payload: dict) -> ProvisionSchoolWithInitialYearCommand:
    return ProvisionSchoolWithInitialYearCommand(
        school_id=payload["school_id"],
        school_name=payload["school_name"],
        academic_year_id=payload["initial_academic_year"]["academic_year_id"],
        academic_year_label=payload["initial_academic_year"]["label"],
    )


@pytest.fixture
def db(live_db_engine):
    connection = live_db_engine.connect()
    transaction = connection.begin()
    session_factory = sessionmaker(
        bind=connection, autoflush=False, autocommit=False, expire_on_commit=False,
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
    def override_session():
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_session
    app.dependency_overrides[get_school_provisioning_service] = lambda: ProvisionSchoolWithInitialYearService(
        SqlAlchemySchoolProvisioningRepository(session_factory),
    )
    app.dependency_overrides[get_configuration_revision_repository] = (
        lambda: SqlAlchemyConfigurationRevisionRepository(session_factory)
    )
    app.dependency_overrides[get_schedule_version_repository] = (
        lambda: SqlAlchemyScheduleVersionRepository(session_factory)
    )
    app.dependency_overrides[get_teacher_service] = lambda: TeacherService(
        SessionFactorySchedulingProblemRepository(session_factory),
        SqlAlchemyTeacherRepository(session_factory),
        id_factory=lambda: "teacher_trial",
    )
    return TestClient(app)


@pytest.fixture
def client(db):
    _session, session_factory = db
    try:
        yield _client(session_factory)
    finally:
        for dependency in (
            get_session,
            get_school_provisioning_service,
            get_configuration_revision_repository,
            get_schedule_version_repository,
            get_teacher_service,
        ):
            app.dependency_overrides.pop(dependency, None)


def _counts(session: Session, school_id: str) -> dict[str, int]:
    session.expire_all()
    school = session.execute(select(m.School).where(m.School.natural_id == school_id)).scalar_one()
    year_ids = select(m.AcademicYear.id).where(m.AcademicYear.school_id == school.id)
    return {
        "schools": session.execute(
            select(func.count()).select_from(m.School).where(m.School.natural_id == school_id)
        ).scalar_one(),
        "years": session.execute(
            select(func.count()).select_from(m.AcademicYear).where(m.AcademicYear.school_id == school.id)
        ).scalar_one(),
        "revisions": session.execute(
            select(func.count()).select_from(m.ConfigurationRevision).where(
                m.ConfigurationRevision.academic_year_id.in_(year_ids)
            )
        ).scalar_one(),
        "schedules": session.execute(
            select(func.count()).select_from(m.Schedule).where(m.Schedule.academic_year_id.in_(year_ids))
        ).scalar_one(),
        "versions": session.execute(
            select(func.count()).select_from(m.ScheduleVersion).where(
                m.ScheduleVersion.academic_year_id.in_(year_ids)
            )
        ).scalar_one(),
    }


def test_post_schools_creates_exact_initial_aggregate_and_replay_is_idempotent(client, db):
    session, _factory = db
    payload = _payload()
    response = client.post("/schools", json=payload)
    assert response.status_code == 201
    assert response.json() == {
        "school": {"id": payload["school_id"], "name": "Provisioned School"},
        "academic_year": {
            "id": payload["initial_academic_year"]["academic_year_id"], "label": "2026/2027",
        },
        "configuration_state": {
            "published_revision_number": None,
            "draft_revision_number": 1,
            "configuration_locked": False,
            "timetable_out_of_date": False,
        },
    }
    assert "has_schedule" not in response.json()["configuration_state"]
    assert _counts(session, payload["school_id"]) == {
        "schools": 1, "years": 1, "revisions": 1, "schedules": 0, "versions": 0,
    }

    session.expire_all()
    school = session.execute(select(m.School).where(m.School.natural_id == payload["school_id"])).scalar_one()
    year = session.execute(select(m.AcademicYear).where(m.AcademicYear.school_id == school.id)).scalar_one()
    revision = session.execute(
        select(m.ConfigurationRevision).where(m.ConfigurationRevision.academic_year_id == year.id)
    ).scalar_one()
    assert (revision.revision_number, revision.status) == (1, "DRAFT")
    assert year.draft_revision_id == revision.id
    assert year.published_revision_id is None

    replay = client.post("/schools", json=payload)
    assert replay.status_code == 201
    assert replay.json() == response.json()
    assert _counts(session, payload["school_id"]) == {
        "schools": 1, "years": 1, "revisions": 1, "schedules": 0, "versions": 0,
    }


def test_provisioned_year_immediately_supports_state_config_crud_and_no_active_schedule(client):
    payload = _payload()
    assert client.post("/schools", json=payload).status_code == 201
    base = f"/schools/{payload['school_id']}/years/{payload['initial_academic_year']['academic_year_id']}"

    state = client.get(f"{base}/configuration/state")
    assert state.status_code == 200
    assert state.json() == {
        "published_revision_number": None, "draft_revision_number": 1,
        "configuration_locked": False, "timetable_out_of_date": False,
    }

    config = client.get(f"{base}/config")
    assert config.status_code == 200
    for key in (
        "days", "periods", "teachers", "class_sections", "participant_groups", "activities",
        "teaching_requirements", "resources", "teacher_availabilities", "reserved_blocks",
        "fixed_placements",
    ):
        assert config.json()[key] == []

    created = client.post(f"{base}/teachers", json={"first_name": "Nino", "last_name": "Beridze"})
    assert created.status_code == 201
    assert created.json()["name"] == "Nino Beridze"
    assert [teacher["name"] for teacher in client.get(f"{base}/config").json()["teachers"]] == [
        "Nino Beridze"
    ]

    active = client.get(f"{base}/schedule/active")
    assert active.status_code == 404
    assert active.json() == {"detail": "Active schedule not found"}


def test_http_conflicts_and_validation_do_not_mutate_persistence(client, db):
    session, _factory = db
    payload = _payload()
    assert client.post("/schools", json=payload).status_code == 201
    expected_counts = _counts(session, payload["school_id"])

    different_name = {**payload, "school_name": "Conflicting School"}
    response = client.post("/schools", json=different_name)
    assert response.status_code == 409
    assert response.json()["code"] == "SCHOOL_ID_ALREADY_EXISTS"

    different_year = {
        **payload,
        "initial_academic_year": {**payload["initial_academic_year"], "label": "Different"},
    }
    response = client.post("/schools", json=different_year)
    assert response.status_code == 409
    assert response.json()["code"] == "ACADEMIC_YEAR_ID_ALREADY_EXISTS"
    assert _counts(session, payload["school_id"]) == expected_counts

    assert client.post("/schools", json={**payload, "school_name": "   "}).status_code == 422
    assert client.post("/schools", json={**payload, "school_id": "Bad ID"}).status_code == 422
    assert _counts(session, payload["school_id"]) == expected_counts


def test_display_values_trim_but_public_ids_are_not_rewritten(client):
    payload = _payload(school_name="  Trimmed School  ")
    payload["initial_academic_year"]["label"] = "  2026/2027  "
    response = client.post("/schools", json=payload)
    assert response.status_code == 201
    assert response.json()["school"]["name"] == "Trimmed School"
    assert response.json()["academic_year"]["label"] == "2026/2027"
    assert client.post("/schools", json={**payload, "school_id": f" {payload['school_id']} "}).status_code == 422


def test_application_validation_error_has_structured_422_mapping(client):
    class InvalidService:
        def provision(self, command):
            raise InvalidSchoolProvisioningError((
                ValidationError("BLANK_SCHOOL_NAME", "school_name is blank after trimming"),
            ))

    app.dependency_overrides[get_school_provisioning_service] = lambda: InvalidService()
    response = client.post("/schools", json=_payload())
    assert response.status_code == 422
    assert response.json()["code"] == "INVALID_SCHOOL_PROVISIONING"
    assert response.json()["errors"][0]["code"] == "BLANK_SCHOOL_NAME"


def test_same_year_natural_id_is_scoped_to_each_school(client):
    year_id = f"ay-shared-{_suffix()}"
    first = _payload()
    first["initial_academic_year"]["academic_year_id"] = year_id
    second = _payload()
    second["initial_academic_year"]["academic_year_id"] = year_id
    assert client.post("/schools", json=first).status_code == 201
    assert client.post("/schools", json=second).status_code == 201


def test_real_transaction_rolls_back_school_insert_when_later_flush_fails(live_db_engine):
    school_id = f"rollback-school-{_suffix()}"

    class FailingAfterSchoolFlushSession(Session):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._provisioning_flushes = 0

        def flush(self, objects=None):
            self._provisioning_flushes += 1
            if self._provisioning_flushes == 2:
                raise RuntimeError("injected failure after School flush")
            return super().flush(objects)

    factory = sessionmaker(
        bind=live_db_engine, class_=FailingAfterSchoolFlushSession,
        autoflush=False, autocommit=False, expire_on_commit=False,
    )
    command = ProvisionSchoolWithInitialYearCommand(
        school_id, "Rollback School", f"ay-{_suffix()}", "2026/2027",
    )
    with pytest.raises(RuntimeError, match="injected failure"):
        SqlAlchemySchoolProvisioningRepository(factory).provision_school_with_initial_year(command)

    with Session(live_db_engine) as check:
        assert check.execute(select(m.School).where(m.School.natural_id == school_id)).scalar_one_or_none() is None
        assert check.execute(
            select(func.count()).select_from(m.AcademicYear).join(m.School).where(m.School.natural_id == school_id)
        ).scalar_one() == 0
        assert check.execute(
            select(func.count())
            .select_from(m.ConfigurationRevision)
            .join(m.AcademicYear, m.AcademicYear.id == m.ConfigurationRevision.academic_year_id)
            .join(m.School, m.School.id == m.AcademicYear.school_id)
            .where(m.School.natural_id == school_id)
        ).scalar_one() == 0


def _run_concurrently(repository, commands):
    barrier = Barrier(len(commands))

    def run(command):
        barrier.wait()
        try:
            return repository.provision_school_with_initial_year(command)
        except Exception as exc:  # returned for deterministic assertion in the parent thread
            return exc

    with ThreadPoolExecutor(max_workers=len(commands)) as executor:
        return list(executor.map(run, commands))


def _cleanup_school(engine, school_id: str) -> None:
    with Session(engine) as session:
        school = session.execute(select(m.School).where(m.School.natural_id == school_id)).scalar_one_or_none()
        if school is not None:
            session.delete(school)
            session.commit()


def test_concurrent_equivalent_provisioning_resolves_to_one_aggregate(live_db_engine):
    payload = _payload()
    command = _command(payload)
    factory = sessionmaker(bind=live_db_engine, autoflush=False, autocommit=False, expire_on_commit=False)
    try:
        results = _run_concurrently(SqlAlchemySchoolProvisioningRepository(factory), [command, command])
        assert all(result == results[0] for result in results)
        with Session(live_db_engine) as session:
            assert _counts(session, command.school_id) == {
                "schools": 1, "years": 1, "revisions": 1, "schedules": 0, "versions": 0,
            }
    finally:
        _cleanup_school(live_db_engine, command.school_id)


def test_concurrent_incompatible_provisioning_has_one_winner_and_one_conflict(live_db_engine):
    payload = _payload()
    winner_or_loser = _command(payload)
    incompatible = ProvisionSchoolWithInitialYearCommand(
        winner_or_loser.school_id,
        "Incompatible Name",
        winner_or_loser.academic_year_id,
        winner_or_loser.academic_year_label,
    )
    factory = sessionmaker(bind=live_db_engine, autoflush=False, autocommit=False, expire_on_commit=False)
    try:
        results = _run_concurrently(
            SqlAlchemySchoolProvisioningRepository(factory), [winner_or_loser, incompatible],
        )
        assert sum(isinstance(result, SchoolProvisioningConflictError) for result in results) == 1
        assert sum(not isinstance(result, Exception) for result in results) == 1
        with Session(live_db_engine) as session:
            assert _counts(session, winner_or_loser.school_id) == {
                "schools": 1, "years": 1, "revisions": 1, "schedules": 0, "versions": 0,
            }
    finally:
        _cleanup_school(live_db_engine, winner_or_loser.school_id)
