"""SQLAlchemy adapters implementing `application.ports.
CalendarDayRepository`/`CalendarPeriodRepository` (Calendar A) -- the
Day/Period catalog write surface over the already-existing `day`/
`period` tables.

Session-factory-backed, exactly like `SqlAlchemyResourceRepository`:
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
shared, unchanged, with every other configuration writer via
`configuration_write_lock.py`.

Both `idx` reassignment sequences (Day delete-reindex, Period
delete-reindex/create/update/move) go through a two-pass, negative-
sentinel `idx` write (see `_reindex_via_sentinel` /
`_write_period_sequence`) so the `UNIQUE(academic_year_id, idx)`
constraint (checked immediately, per-statement, not deferred) is never
transiently violated mid-reassignment.
"""
from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from school_timetable.application.calendar_models import DayWriteResult, PeriodFields, PeriodWriteResult
from school_timetable.application.errors import DayNotFoundError, PeriodNotFoundError
from school_timetable.domain.calendar import derive_starts_new_block, recompute_block_ids
from school_timetable.domain.problem import SchedulingProblem
from school_timetable.persistence import models as orm
from school_timetable.persistence.configuration_write_lock import (
    lock_academic_year,
    reject_if_configuration_locked,
    resolve_year_id,
)
from school_timetable.persistence.problem_repository import SqlAlchemySchedulingProblemRepository


def _ordered_day_rows(session: Session, year_id: int) -> list[orm.Day]:
    rows = list(session.execute(select(orm.Day).where(orm.Day.academic_year_id == year_id)).scalars())
    return sorted(rows, key=lambda r: r.idx)


def _ordered_period_rows(session: Session, year_id: int) -> list[orm.Period]:
    rows = list(session.execute(select(orm.Period).where(orm.Period.academic_year_id == year_id)).scalars())
    return sorted(rows, key=lambda r: r.idx)


def _reindex_via_sentinel(session: Session, ordered_rows: list) -> None:
    """Two-pass `idx` write: negative sentinels first (guaranteed to
    never collide with any real, always-non-negative `idx`), then the
    true `0..N-1` positions -- avoids ever violating
    `UNIQUE(academic_year_id, idx)` mid-reassignment, regardless of
    which rows moved."""
    for position, row in enumerate(ordered_rows):
        row.idx = -(position + 1)
    session.flush()
    for position, row in enumerate(ordered_rows):
        row.idx = position
    session.flush()


def _write_period_sequence(
    session: Session, ordered_rows: list[orm.Period], marker_by_id: dict[str, bool],
) -> None:
    """The single, shared tail of every Period write (create/update/
    delete/move): reindexes `idx` (sentinel-safe) and recomputes
    `block_id` for the complete new sequence via
    `domain.calendar.recompute_block_ids`."""
    ordered_ids = [row.natural_id for row in ordered_rows]
    block_assignment = recompute_block_ids(ordered_ids, marker_by_id)
    for position, row in enumerate(ordered_rows):
        row.idx = -(position + 1)
    session.flush()
    for position, row in enumerate(ordered_rows):
        row.idx = position
        row.block_id = block_assignment[row.natural_id]
    session.flush()


class SqlAlchemyCalendarDayRepository:
    """Implements `application.ports.CalendarDayRepository` structurally
    (a `Protocol` -- no inheritance needed)."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def create(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        day_natural_id: str,
        name: str,
        validate: Callable[[SchedulingProblem], None],
    ) -> DayWriteResult:
        session = self._session_factory()
        try:
            year_id = resolve_year_id(session, school_natural_id, academic_year_natural_id)
            lock_academic_year(session, year_id)
            reject_if_configuration_locked(session, year_id, school_natural_id, academic_year_natural_id)

            current_problem = SqlAlchemySchedulingProblemRepository(session).load_by_school_and_year(
                school_natural_id, academic_year_natural_id,
            )
            validate(current_problem)

            next_index = len(current_problem.days)
            session.add(orm.Day(
                academic_year_id=year_id, natural_id=day_natural_id, name=name, idx=next_index,
            ))
            session.commit()
            return DayWriteResult(id=day_natural_id, name=name, index=next_index)
        except BaseException:
            session.rollback()
            raise
        finally:
            session.close()

    def update(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        day_natural_id: str,
        name: str,
        validate: Callable[[SchedulingProblem], None],
    ) -> DayWriteResult:
        session = self._session_factory()
        try:
            year_id = resolve_year_id(session, school_natural_id, academic_year_natural_id)
            lock_academic_year(session, year_id)
            reject_if_configuration_locked(session, year_id, school_natural_id, academic_year_natural_id)

            current_problem = SqlAlchemySchedulingProblemRepository(session).load_by_school_and_year(
                school_natural_id, academic_year_natural_id,
            )
            validate(current_problem)

            row = session.execute(
                select(orm.Day).where(
                    orm.Day.academic_year_id == year_id, orm.Day.natural_id == day_natural_id,
                )
            ).scalar_one_or_none()
            if row is None:
                raise DayNotFoundError(school_natural_id, academic_year_natural_id, day_natural_id)
            row.name = name
            session.commit()
            return DayWriteResult(id=day_natural_id, name=name, index=row.idx)
        except BaseException:
            session.rollback()
            raise
        finally:
            session.close()

    def delete(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        day_natural_id: str,
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

            rows = _ordered_day_rows(session, year_id)
            target = next((r for r in rows if r.natural_id == day_natural_id), None)
            if target is None:
                raise DayNotFoundError(school_natural_id, academic_year_natural_id, day_natural_id)
            rows.remove(target)
            session.delete(target)
            session.flush()
            _reindex_via_sentinel(session, rows)
            session.commit()
        except BaseException:
            session.rollback()
            raise
        finally:
            session.close()

    def move(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        day_natural_id: str,
        direction: str,
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

            rows = _ordered_day_rows(session, year_id)
            position = next((i for i, r in enumerate(rows) if r.natural_id == day_natural_id), None)
            if position is None:
                raise DayNotFoundError(school_natural_id, academic_year_natural_id, day_natural_id)
            neighbor_position = position - 1 if direction == "up" else position + 1

            row_a, row_b = rows[position], rows[neighbor_position]
            old_a_idx, old_b_idx = row_a.idx, row_b.idx
            row_a.idx = -1
            session.flush()
            row_b.idx = old_a_idx
            session.flush()
            row_a.idx = old_b_idx
            session.flush()
            session.commit()
        except BaseException:
            session.rollback()
            raise
        finally:
            session.close()


class SqlAlchemyCalendarPeriodRepository:
    """Implements `application.ports.CalendarPeriodRepository`
    structurally (a `Protocol` -- no inheritance needed)."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def create(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        period_natural_id: str,
        fields: PeriodFields,
        validate: Callable[[SchedulingProblem], None],
    ) -> PeriodWriteResult:
        session = self._session_factory()
        try:
            year_id = resolve_year_id(session, school_natural_id, academic_year_natural_id)
            lock_academic_year(session, year_id)
            reject_if_configuration_locked(session, year_id, school_natural_id, academic_year_natural_id)

            current_problem = SqlAlchemySchedulingProblemRepository(session).load_by_school_and_year(
                school_natural_id, academic_year_natural_id,
            )
            validate(current_problem)

            markers = derive_starts_new_block(current_problem.periods)
            markers[period_natural_id] = fields.starts_new_block

            new_row = orm.Period(
                academic_year_id=year_id, natural_id=period_natural_id, name=fields.name,
                idx=len(current_problem.periods), block_id="", is_instructional=True,
                start_time=fields.start_time, end_time=fields.end_time,
            )
            session.add(new_row)
            session.flush()

            rows = _ordered_period_rows(session, year_id)
            _write_period_sequence(session, rows, markers)
            session.commit()

            return PeriodWriteResult(
                id=period_natural_id, name=fields.name, index=new_row.idx, start_time=fields.start_time,
                end_time=fields.end_time, starts_new_block=fields.starts_new_block, is_instructional=True,
            )
        except BaseException:
            session.rollback()
            raise
        finally:
            session.close()

    def update(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        period_natural_id: str,
        fields: PeriodFields,
        validate: Callable[[SchedulingProblem], None],
    ) -> PeriodWriteResult:
        session = self._session_factory()
        try:
            year_id = resolve_year_id(session, school_natural_id, academic_year_natural_id)
            lock_academic_year(session, year_id)
            reject_if_configuration_locked(session, year_id, school_natural_id, academic_year_natural_id)

            current_problem = SqlAlchemySchedulingProblemRepository(session).load_by_school_and_year(
                school_natural_id, academic_year_natural_id,
            )
            validate(current_problem)

            row = session.execute(
                select(orm.Period).where(
                    orm.Period.academic_year_id == year_id, orm.Period.natural_id == period_natural_id,
                )
            ).scalar_one_or_none()
            if row is None:
                raise PeriodNotFoundError(school_natural_id, academic_year_natural_id, period_natural_id)

            row.name = fields.name
            row.start_time = fields.start_time
            row.end_time = fields.end_time
            # `is_instructional` is deliberately never assigned here --
            # the locked policy preserves whatever this row already had
            # (`True` for every Calendar-A-created Period, `False` only
            # ever possible for a legacy fixture-seeded row).

            markers = derive_starts_new_block(current_problem.periods)
            markers[period_natural_id] = fields.starts_new_block

            rows = _ordered_period_rows(session, year_id)
            _write_period_sequence(session, rows, markers)
            session.commit()

            return PeriodWriteResult(
                id=period_natural_id, name=fields.name, index=row.idx, start_time=fields.start_time,
                end_time=fields.end_time, starts_new_block=fields.starts_new_block,
                is_instructional=row.is_instructional,
            )
        except BaseException:
            session.rollback()
            raise
        finally:
            session.close()

    def delete(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        period_natural_id: str,
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

            markers = derive_starts_new_block(current_problem.periods)
            markers.pop(period_natural_id, None)

            rows = _ordered_period_rows(session, year_id)
            target = next((r for r in rows if r.natural_id == period_natural_id), None)
            if target is None:
                raise PeriodNotFoundError(school_natural_id, academic_year_natural_id, period_natural_id)
            rows.remove(target)
            session.delete(target)
            session.flush()

            _write_period_sequence(session, rows, markers)
            session.commit()
        except BaseException:
            session.rollback()
            raise
        finally:
            session.close()

    def move(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        period_natural_id: str,
        direction: str,
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

            # Each Period's own `starts_new_block` marker travels with
            # its row identity across the swap -- computed from the
            # pre-mutation order, then applied to the post-swap order
            # (see `domain.calendar.recompute_block_ids`'s docstring).
            markers = derive_starts_new_block(current_problem.periods)

            rows = _ordered_period_rows(session, year_id)
            position = next((i for i, r in enumerate(rows) if r.natural_id == period_natural_id), None)
            if position is None:
                raise PeriodNotFoundError(school_natural_id, academic_year_natural_id, period_natural_id)
            neighbor_position = position - 1 if direction == "up" else position + 1
            rows[position], rows[neighbor_position] = rows[neighbor_position], rows[position]

            _write_period_sequence(session, rows, markers)
            session.commit()
        except BaseException:
            session.rollback()
            raise
        finally:
            session.close()
