"""SQLAlchemy adapter implementing `application.ports.TeacherRepository`
(Real-School Setup MVP Slice B).

Session-factory-backed, exactly like
`SqlAlchemyTeachingAssignmentRepository`: every public method opens and
closes its own short `Session`. Each method is one atomic transaction
that, in this exact order: (1) resolves the `AcademicYear` surrogate
ID; (2) acquires the shared `SELECT ... FOR UPDATE` row lock on that
`AcademicYear` (Owner Decision #36); (3) authoritatively rechecks,
under that lock, that no `Schedule` exists for this year (Decision
#35); (4) reloads the current authoritative `SchedulingProblem` under
the same lock and invokes the caller-supplied `validate` callback
against it -- never against a stale, pre-lock snapshot; (5) only if
`validate` does not raise, performs the write and commits, still
holding the lock until that commit. Any failure rolls back the whole
transaction. (1)-(3) are shared, unchanged, with
`SqlAlchemyTeachingAssignmentRepository` via `configuration_write_lock.py`.
"""
from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from school_timetable.application.errors import TeacherNotFoundError
from school_timetable.domain.problem import SchedulingProblem
from school_timetable.persistence import models as orm
from school_timetable.persistence.configuration_write_lock import (
    lock_academic_year,
    reject_if_configuration_locked,
    resolve_year_id,
)
from school_timetable.persistence.problem_repository import SqlAlchemySchedulingProblemRepository


class SqlAlchemyTeacherRepository:
    """Implements `application.ports.TeacherRepository` structurally (a
    `Protocol` -- no inheritance needed)."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def create(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        natural_id: str,
        first_name: str,
        last_name: str,
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

            session.add(orm.Teacher(
                academic_year_id=year_id,
                natural_id=natural_id,
                first_name=first_name,
                last_name=last_name,
                ordinal=_next_teacher_ordinal(session, year_id),
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
        natural_id: str,
        first_name: str,
        last_name: str,
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
            # `validate` (application-owned) already confirms `natural_id`
            # exists -- the row lookup below is therefore never expected
            # to miss, but is still checked explicitly rather than
            # trusting a raw SQLAlchemy `NoResultFound`.
            validate(current_problem)

            row = session.execute(
                select(orm.Teacher).where(
                    orm.Teacher.academic_year_id == year_id,
                    orm.Teacher.natural_id == natural_id,
                )
            ).scalar_one_or_none()
            if row is None:
                raise TeacherNotFoundError(school_natural_id, academic_year_natural_id, natural_id)
            row.first_name = first_name
            row.last_name = last_name
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
        natural_id: str,
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
            # `validate` re-confirms existence AND that no current
            # configuration reference blocks the delete (`TeacherInUseError`).
            validate(current_problem)

            row = session.execute(
                select(orm.Teacher).where(
                    orm.Teacher.academic_year_id == year_id,
                    orm.Teacher.natural_id == natural_id,
                )
            ).scalar_one_or_none()
            if row is None:
                raise TeacherNotFoundError(school_natural_id, academic_year_natural_id, natural_id)
            session.delete(row)
            session.commit()
        except BaseException:
            session.rollback()
            raise
        finally:
            session.close()


def _next_teacher_ordinal(session: Session, year_id: int) -> int:
    max_ordinal = session.execute(
        select(func.max(orm.Teacher.ordinal)).where(orm.Teacher.academic_year_id == year_id)
    ).scalar()
    return 0 if max_ordinal is None else max_ordinal + 1
