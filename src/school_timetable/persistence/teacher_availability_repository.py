"""SQLAlchemy adapter implementing
`application.ports.TeacherAvailabilityRepository` (Owner Decision
#38).

Session-factory-backed, exactly like `SqlAlchemyTeacherRepository`:
the one public method opens and closes its own short `Session`. It is
one atomic transaction that, in this exact order: (1) resolves the
`AcademicYear` surrogate ID; (2) acquires the shared `SELECT ... FOR
UPDATE` row lock on that `AcademicYear` (Owner Decision #36); (3)
authoritatively rechecks, under that lock, that no `Schedule` exists
for this year (Decision #35); (4) reloads the current authoritative
`SchedulingProblem` under the same lock and invokes the caller-
supplied `validate` callback against it -- never against a stale,
pre-lock snapshot; (5) only if `validate` does not raise, reconciles
the persisted `TeacherAvailability` rows for the target Teacher and
commits, still holding the lock until that commit. Any failure rolls
back the whole transaction. (1)-(3) are shared, unchanged, with every
other configuration writer via `configuration_write_lock.py`.
"""
from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from school_timetable.application.errors import TeacherNotFoundError
from school_timetable.application.teacher_availability_models import TeacherAvailabilityExceptionFields
from school_timetable.domain.problem import SchedulingProblem
from school_timetable.persistence import models as orm
from school_timetable.persistence.configuration_write_lock import (
    lock_academic_year,
    reject_if_configuration_locked,
    resolve_year_id,
)
from school_timetable.persistence.problem_repository import SqlAlchemySchedulingProblemRepository


class SqlAlchemyTeacherAvailabilityRepository:
    """Implements `application.ports.TeacherAvailabilityRepository`
    structurally (a `Protocol` -- no inheritance needed)."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def replace_exceptions(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        teacher_natural_id: str,
        exceptions: tuple[TeacherAvailabilityExceptionFields, ...],
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
            # `teacher_natural_id` and every `day_id`/`period_id`
            # referenced by `exceptions` exist -- the lookups below are
            # therefore never expected to miss, but the Teacher lookup
            # is still checked explicitly rather than trusting a raw
            # SQLAlchemy `NoResultFound`.
            validate(current_problem)

            teacher_row = session.execute(
                select(orm.Teacher).where(
                    orm.Teacher.academic_year_id == year_id,
                    orm.Teacher.natural_id == teacher_natural_id,
                )
            ).scalar_one_or_none()
            if teacher_row is None:
                raise TeacherNotFoundError(school_natural_id, academic_year_natural_id, teacher_natural_id)

            day_rows = list(session.execute(select(orm.Day).where(orm.Day.academic_year_id == year_id)).scalars())
            day_surrogate_by_natural = {row.natural_id: row.id for row in day_rows}
            day_idx_by_surrogate = {row.id: row.idx for row in day_rows}

            period_rows = list(
                session.execute(select(orm.Period).where(orm.Period.academic_year_id == year_id)).scalars()
            )
            period_surrogate_by_natural = {row.natural_id: row.id for row in period_rows}
            period_idx_by_surrogate = {row.id: row.idx for row in period_rows}

            desired: dict[tuple[int, int], str] = {
                (day_surrogate_by_natural[e.day_id], period_surrogate_by_natural[e.period_id]): e.status
                for e in exceptions
            }

            existing_rows = list(
                session.execute(
                    select(orm.TeacherAvailability).where(
                        orm.TeacherAvailability.academic_year_id == year_id,
                        orm.TeacherAvailability.teacher_id == teacher_row.id,
                    )
                ).scalars()
            )
            existing_by_cell = {(row.day_id, row.period_id): row for row in existing_rows}

            for cell, row in existing_by_cell.items():
                if cell not in desired:
                    session.delete(row)
                elif row.status != desired[cell]:
                    row.status = desired[cell]

            new_cells = sorted(
                (cell for cell in desired if cell not in existing_by_cell),
                key=lambda cell: (day_idx_by_surrogate[cell[0]], period_idx_by_surrogate[cell[1]]),
            )
            next_ordinal = _next_availability_ordinal(session, year_id)
            for day_surrogate, period_surrogate in new_cells:
                session.add(orm.TeacherAvailability(
                    academic_year_id=year_id,
                    teacher_id=teacher_row.id,
                    day_id=day_surrogate,
                    period_id=period_surrogate,
                    status=desired[(day_surrogate, period_surrogate)],
                    ordinal=next_ordinal,
                ))
                next_ordinal += 1

            session.commit()
        except BaseException:
            session.rollback()
            raise
        finally:
            session.close()


def _next_availability_ordinal(session: Session, year_id: int) -> int:
    """The next `TeacherAvailability.ordinal` value, computed across
    every Teacher's rows in this `AcademicYear` -- never scoped to only
    the Teacher being written -- matching the table's own
    `UniqueConstraint(academic_year_id, ordinal)`. Deleting a cell
    leaves a gap; surviving/updated rows are never renumbered."""
    max_ordinal = session.execute(
        select(func.max(orm.TeacherAvailability.ordinal)).where(
            orm.TeacherAvailability.academic_year_id == year_id,
        )
    ).scalar()
    return 0 if max_ordinal is None else max_ordinal + 1
