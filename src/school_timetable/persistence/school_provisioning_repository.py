"""SQLAlchemy adapter for atomic School/initial-year provisioning."""
from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from school_timetable.application.configuration_revision_models import ConfigurationRevisionState
from school_timetable.application.errors import (
    AcademicYearProvisioningConflictError,
    SchoolProvisioningConflictError,
)
from school_timetable.application.school_provisioning_models import (
    ProvisionedAcademicYear,
    ProvisionedSchool,
    ProvisionSchoolWithInitialYearCommand,
    ProvisionSchoolWithInitialYearResult,
)
from school_timetable.persistence import models as orm


_PROVISIONING_UNIQUE_CONSTRAINTS = {
    "uq_school_natural_id",
    "uq_academic_year_school_natural_id",
}


class SqlAlchemySchoolProvisioningRepository:
    """Creates the complete initial aggregate in one transaction."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def provision_school_with_initial_year(
        self, command: ProvisionSchoolWithInitialYearCommand,
    ) -> ProvisionSchoolWithInitialYearResult:
        session = self._session_factory()
        try:
            existing = _load_existing_or_conflict(session, command)
            if existing is not None:
                return existing

            school = orm.School(natural_id=command.school_id, name=command.school_name)
            session.add(school)
            session.flush()

            year = orm.AcademicYear(
                school_id=school.id,
                natural_id=command.academic_year_id,
                label=command.academic_year_label,
                published_revision_id=None,
                draft_revision_id=None,
            )
            session.add(year)
            session.flush()

            revision = orm.ConfigurationRevision(
                academic_year_id=year.id,
                revision_number=1,
                status="DRAFT",
            )
            session.add(revision)
            session.flush()
            year.draft_revision_id = revision.id
            session.flush()

            result = _result(command)
            session.commit()
            return result
        except IntegrityError as exc:
            session.rollback()
            if _constraint_name(exc) not in _PROVISIONING_UNIQUE_CONSTRAINTS:
                raise
            # A concurrent equivalent request may have won the unique-key
            # race.  Return only a complete, exactly equivalent aggregate;
            # otherwise expose the appropriate application conflict.
            return _load_existing_or_conflict(session, command, require_existing=True)
        except BaseException:
            session.rollback()
            raise
        finally:
            session.close()


def _constraint_name(exc: IntegrityError) -> str | None:
    diagnostic = getattr(getattr(exc, "orig", None), "diag", None)
    return getattr(diagnostic, "constraint_name", None)


def _load_existing_or_conflict(
    session: Session,
    command: ProvisionSchoolWithInitialYearCommand,
    *,
    require_existing: bool = False,
) -> ProvisionSchoolWithInitialYearResult | None:
    school = session.execute(
        select(orm.School).where(orm.School.natural_id == command.school_id)
    ).scalar_one_or_none()
    if school is None:
        if require_existing:
            # The named unique violation said a provisioning identity
            # collided, yet the complete school identity cannot be reloaded.
            # This is not an idempotent replay and must not be disguised.
            raise RuntimeError("provisioning uniqueness conflict could not be resolved")
        return None
    if school.name != command.school_name:
        raise SchoolProvisioningConflictError(command.school_id)

    year = session.execute(
        select(orm.AcademicYear).where(
            orm.AcademicYear.school_id == school.id,
            orm.AcademicYear.natural_id == command.academic_year_id,
        )
    ).scalar_one_or_none()
    if year is None:
        # This operation provisions a new School and its first year as one
        # aggregate.  It never silently adds a year to a pre-existing School.
        raise SchoolProvisioningConflictError(command.school_id)
    if year.label != command.academic_year_label:
        raise AcademicYearProvisioningConflictError(command.school_id, command.academic_year_id)

    revision = session.execute(
        select(orm.ConfigurationRevision).where(
            orm.ConfigurationRevision.academic_year_id == year.id,
            orm.ConfigurationRevision.revision_number == 1,
        )
    ).scalar_one_or_none()
    revision_count = session.execute(
        select(func.count()).select_from(orm.ConfigurationRevision).where(
            orm.ConfigurationRevision.academic_year_id == year.id,
        )
    ).scalar_one()
    schedule_exists = session.execute(
        select(orm.Schedule.id).where(orm.Schedule.academic_year_id == year.id).limit(1)
    ).scalar_one_or_none() is not None
    if (
        revision is None
        or revision_count != 1
        or revision.status != "DRAFT"
        or year.draft_revision_id != revision.id
        or year.published_revision_id is not None
        or schedule_exists
    ):
        raise AcademicYearProvisioningConflictError(command.school_id, command.academic_year_id)
    return _result(command)


def _result(command: ProvisionSchoolWithInitialYearCommand) -> ProvisionSchoolWithInitialYearResult:
    return ProvisionSchoolWithInitialYearResult(
        school=ProvisionedSchool(id=command.school_id, name=command.school_name),
        academic_year=ProvisionedAcademicYear(
            id=command.academic_year_id, label=command.academic_year_label,
        ),
        configuration_state=ConfigurationRevisionState(
            published_revision_number=None,
            draft_revision_number=1,
            has_schedule=False,
            configuration_locked=False,
            timetable_out_of_date=False,
        ),
    )
