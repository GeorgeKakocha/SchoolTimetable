"""SQLAlchemy adapter implementing
`application.ports.SpecialActivityRepository` (Reserved Activities
Slice A1) -- the user-facing "Special Activity" write surface over
`Activity(kind=CLUB)` rows only.

Session-factory-backed, exactly like `SqlAlchemyActivityRepository`:
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
shared, unchanged, with `SqlAlchemyActivityRepository`/every other
configuration writer via `configuration_write_lock.py`.

`update`/`delete` authoritatively re-verify `row.kind == "CLUB"`
against the just-resolved ORM row (never trusting the domain-level
`validate` callback alone for this) -- an `ORDINARY` row is treated as
absent, raising `SpecialActivityNotFoundError`, never mutated.

`update` additionally synchronizes every persisted `ReservedBlock.name`
referencing the renamed Activity's surrogate ID, in the SAME
transaction as the Activity rename -- `ReservedBlock.name` is a
server-derived mirror of its Activity's `name`, never independent
input (the corrected product contract), so it must never go stale.
Only blocks referencing this exact Activity are touched; blocks
referencing any other Activity, and rows in any other AcademicYear,
are structurally excluded by the `WHERE` clause itself.
"""
from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from school_timetable.application.errors import SpecialActivityNotFoundError
from school_timetable.domain.problem import SchedulingProblem
from school_timetable.persistence import models as orm
from school_timetable.persistence.configuration_write_lock import (
    lock_academic_year,
    reject_if_configuration_locked,
    resolve_year_id,
)
from school_timetable.persistence.problem_repository import SqlAlchemySchedulingProblemRepository

_CLUB = "CLUB"


class SqlAlchemySpecialActivityRepository:
    """Implements `application.ports.SpecialActivityRepository`
    structurally (a `Protocol` -- no inheritance needed)."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def create(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        special_activity_natural_id: str,
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

            session.add(orm.Activity(
                academic_year_id=year_id, natural_id=special_activity_natural_id,
                name=name, kind=_CLUB, ordinal=_next_activity_ordinal(session, year_id),
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
        special_activity_natural_id: str,
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
            # `special_activity_natural_id` resolves to a CLUB
            # activity -- the row lookup below is therefore never
            # expected to miss or be ORDINARY, but both are still
            # checked explicitly rather than trusting the domain-level
            # check alone.
            validate(current_problem)

            row = session.execute(
                select(orm.Activity).where(
                    orm.Activity.academic_year_id == year_id,
                    orm.Activity.natural_id == special_activity_natural_id,
                )
            ).scalar_one_or_none()
            if row is None or row.kind != _CLUB:
                raise SpecialActivityNotFoundError(
                    school_natural_id, academic_year_natural_id, special_activity_natural_id,
                )
            row.name = name

            # Mandatory derived-name synchronization -- same
            # transaction, same commit. Only rows whose `activity_id`
            # is this exact Activity's own surrogate ID are touched;
            # the composite `academic_year_id` filter is redundant
            # with `activity_id` alone (an Activity's surrogate ID is
            # already unique) but kept for defense-in-depth clarity and
            # symmetry with every other query in this module.
            session.execute(
                update(orm.ReservedBlock)
                .where(
                    orm.ReservedBlock.academic_year_id == year_id,
                    orm.ReservedBlock.activity_id == row.id,
                )
                .values(name=name)
            )
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
        special_activity_natural_id: str,
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
            # `validate` re-confirms the target is CLUB AND that no
            # current configuration reference blocks the delete
            # (`SpecialActivityInUseError`).
            validate(current_problem)

            row = session.execute(
                select(orm.Activity).where(
                    orm.Activity.academic_year_id == year_id,
                    orm.Activity.natural_id == special_activity_natural_id,
                )
            ).scalar_one_or_none()
            if row is None or row.kind != _CLUB:
                raise SpecialActivityNotFoundError(
                    school_natural_id, academic_year_natural_id, special_activity_natural_id,
                )
            session.delete(row)
            session.commit()
        except BaseException:
            session.rollback()
            raise
        finally:
            session.close()


def _next_activity_ordinal(session: Session, year_id: int) -> int:
    """Ordinal belongs to the entire `activity` table -- computed
    across BOTH `ORDINARY` and `CLUB` rows, never `CLUB` alone, one
    shared ordering domain matching `problem_repository.py`'s existing
    loader and `activity_repository.py`'s own identical helper. Kept
    local to this repository rather than imported from
    `activity_repository.py`, per `configuration_write_lock.py`'s own
    documented precedent that ordinal helpers stay local to each
    repository."""
    max_ordinal = session.execute(
        select(func.max(orm.Activity.ordinal)).where(orm.Activity.academic_year_id == year_id)
    ).scalar()
    return 0 if max_ordinal is None else max_ordinal + 1
