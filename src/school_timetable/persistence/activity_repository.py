"""SQLAlchemy adapter implementing `application.ports.ActivityRepository`
(Real-School Setup MVP Slice D) -- the user-facing "Subject" write
surface over `Activity(kind=ORDINARY)` rows only.

Session-factory-backed, exactly like `SqlAlchemyClassSectionRepository`:
every public method opens and closes its own short `Session`. Each
method is one atomic transaction that, in this exact order: (1)
resolves the `AcademicYear` surrogate ID; (2) acquires the shared
`SELECT ... FOR UPDATE` row lock on that `AcademicYear` (Owner Decision
#36); (3) authoritatively rechecks, under that lock, that no `Schedule`
exists for this year (Decision #35); (4) reloads the current
authoritative `SchedulingProblem` under the same lock and invokes the
caller-supplied `validate` callback against it -- never against a
stale, pre-lock snapshot; (5) only if `validate` does not raise,
performs the write and commits, still holding the lock until that
commit. Any failure rolls back the whole transaction. (1)-(3) are
shared, unchanged, with `SqlAlchemyClassSectionRepository`/
`SqlAlchemyTeacherRepository`/`SqlAlchemyTeachingAssignmentRepository`
via `configuration_write_lock.py`.

`update`/`delete` authoritatively re-verify `row.kind == "ORDINARY"`
against the just-resolved ORM row (never trusting the domain-level
`validate` callback alone for this) -- a `CLUB` row is treated as
absent, raising `SubjectNotFoundError`, never mutated.
"""
from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from school_timetable.application.errors import SubjectNotFoundError
from school_timetable.domain.problem import SchedulingProblem
from school_timetable.persistence import models as orm
from school_timetable.persistence.configuration_write_lock import (
    lock_academic_year,
    reject_if_configuration_locked,
    resolve_draft_revision_id,
    resolve_year_id,
)
from school_timetable.persistence.problem_repository import SqlAlchemySchedulingProblemRepository

_ORDINARY = "ORDINARY"


class SqlAlchemyActivityRepository:
    """Implements `application.ports.ActivityRepository` structurally (a
    `Protocol` -- no inheritance needed)."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def create(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        subject_natural_id: str,
        name: str,
        validate: Callable[[SchedulingProblem], None],
    ) -> None:
        session = self._session_factory()
        try:
            year_id = resolve_year_id(session, school_natural_id, academic_year_natural_id)
            lock_academic_year(session, year_id)
            reject_if_configuration_locked(session, year_id, school_natural_id, academic_year_natural_id)

            current_problem = SqlAlchemySchedulingProblemRepository(session).load_by_school_and_year(
                school_natural_id, academic_year_natural_id,
            )
            validate(current_problem)

            revision_id = resolve_draft_revision_id(session, year_id, school_natural_id, academic_year_natural_id)
            session.add(orm.Activity(
                academic_year_id=year_id, configuration_revision_id=revision_id, natural_id=subject_natural_id,
                name=name, kind=_ORDINARY, ordinal=_next_activity_ordinal(session, year_id),
            ))
            session.commit()
        except BaseException:
            session.rollback()
            raise
        finally:
            session.close()

    def update(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        subject_natural_id: str,
        name: str,
        validate: Callable[[SchedulingProblem], None],
    ) -> None:
        session = self._session_factory()
        try:
            year_id = resolve_year_id(session, school_natural_id, academic_year_natural_id)
            lock_academic_year(session, year_id)
            reject_if_configuration_locked(session, year_id, school_natural_id, academic_year_natural_id)

            current_problem = SqlAlchemySchedulingProblemRepository(session).load_by_school_and_year(
                school_natural_id, academic_year_natural_id,
            )
            # `validate` (application-owned) already confirms
            # `subject_natural_id` resolves to an ORDINARY activity --
            # the row lookup below is therefore never expected to miss
            # or be CLUB, but both are still checked explicitly rather
            # than trusting the domain-level check alone.
            validate(current_problem)

            row = session.execute(
                select(orm.Activity).where(
                    orm.Activity.academic_year_id == year_id,
                    orm.Activity.natural_id == subject_natural_id,
                )
            ).scalar_one_or_none()
            if row is None or row.kind != _ORDINARY:
                raise SubjectNotFoundError(school_natural_id, academic_year_natural_id, subject_natural_id)
            row.name = name
            session.commit()
        except BaseException:
            session.rollback()
            raise
        finally:
            session.close()

    def delete(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        subject_natural_id: str,
        validate: Callable[[SchedulingProblem], None],
    ) -> None:
        session = self._session_factory()
        try:
            year_id = resolve_year_id(session, school_natural_id, academic_year_natural_id)
            lock_academic_year(session, year_id)
            reject_if_configuration_locked(session, year_id, school_natural_id, academic_year_natural_id)

            current_problem = SqlAlchemySchedulingProblemRepository(session).load_by_school_and_year(
                school_natural_id, academic_year_natural_id,
            )
            # `validate` re-confirms the target is ORDINARY AND that no
            # current configuration reference blocks the delete
            # (`SubjectInUseError`).
            validate(current_problem)

            row = session.execute(
                select(orm.Activity).where(
                    orm.Activity.academic_year_id == year_id,
                    orm.Activity.natural_id == subject_natural_id,
                )
            ).scalar_one_or_none()
            if row is None or row.kind != _ORDINARY:
                raise SubjectNotFoundError(school_natural_id, academic_year_natural_id, subject_natural_id)
            session.delete(row)
            session.commit()
        except BaseException:
            session.rollback()
            raise
        finally:
            session.close()


def _next_activity_ordinal(session: Session, year_id: int) -> int:
    """Ordinal belongs to the entire `activity` table -- computed
    across BOTH `ORDINARY` and `CLUB` rows, never `ORDINARY` alone, one
    shared ordering domain matching `problem_repository.py`'s existing
    loader."""
    max_ordinal = session.execute(
        select(func.max(orm.Activity.ordinal)).where(orm.Activity.academic_year_id == year_id)
    ).scalar()
    return 0 if max_ordinal is None else max_ordinal + 1
