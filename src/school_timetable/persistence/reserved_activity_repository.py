"""SQLAlchemy adapter implementing
`application.ports.ReservedActivityRepository` (Reserved Activities
Slice A2) -- the user-facing "Reserved Activity" write surface over
`ReservedBlock` rows only.

Session-factory-backed, exactly like `SqlAlchemySpecialActivityRepository`:
every public method opens and closes its own short `Session`. Each
method is one atomic transaction that, in this exact order: (1)
resolves the `AcademicYear` surrogate ID; (2) acquires the shared
`SELECT ... FOR UPDATE` row lock on that `AcademicYear` (Owner Decision
#36); (3) authoritatively rechecks, under that lock, that no `Schedule`
exists for this year (Decision #35); (4) reloads the current
authoritative `SchedulingProblem` under the same lock and invokes the
caller-supplied `validate` callback against it -- never against a
stale, pre-lock snapshot; (5) resolves every referenced ORM row scoped
to the SAME `academic_year_id` (never trusting a bare natural-ID
lookup that could cross AY boundaries), independently re-verifying the
referenced Special Activity's `kind == 'CLUB'` regardless of what
`validate` already confirmed; (6) only if every reference resolves,
performs the write and commits, still holding the lock until that
commit. Any failure rolls back the whole transaction.

`create`/`update` always replace `ReservedBlockClassSection`/
`ReservedBlockSlot` children wholesale -- delete every existing child
row for the target block, then insert the canonically-ordered
replacement set (`class_sections` sorted by the referenced
`ClassSection.ordinal`; `slots` sorted by `(Day.idx, Period.idx)`).
There is no independent child identity to diff against, and canonical
ordering is what keeps two semantically-identical writes (same class
set / slot set, different request order) persist and reload as
byte-identical `SchedulingProblem.reserved_blocks` tuples -- required
for Owner Decision #36's frozen-dataclass generation-race equality to
hold regardless of request order.

`ReservedBlock.name` is always recomputed from the resolved Special
Activity's current `name` -- never accepted as write input.
"""
from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import delete as sql_delete
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from school_timetable.application.errors import (
    NonSpecialActivityTargetError,
    ReservedActivityNotFoundError,
    UnknownReferenceError,
)
from school_timetable.application.reserved_activity_models import (
    ReservedActivityFields,
    ReservedActivitySlotFields,
    ReservedActivityWriteResult,
)
from school_timetable.domain.problem import SchedulingProblem
from school_timetable.persistence import models as orm
from school_timetable.persistence.configuration_write_lock import (
    lock_academic_year,
    reject_if_configuration_locked,
    resolve_year_id,
)
from school_timetable.persistence.problem_repository import SqlAlchemySchedulingProblemRepository

_CLUB = "CLUB"


class SqlAlchemyReservedActivityRepository:
    """Implements `application.ports.ReservedActivityRepository`
    structurally (a `Protocol` -- no inheritance needed)."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def create(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        reserved_activity_natural_id: str,
        fields: ReservedActivityFields,
        validate: Callable[[SchedulingProblem], None],
    ) -> ReservedActivityWriteResult:
        session = self._session_factory()
        try:
            year_id = resolve_year_id(session, school_natural_id, academic_year_natural_id)
            lock_academic_year(session, year_id)
            reject_if_configuration_locked(session, year_id, school_natural_id, academic_year_natural_id)

            current_problem = SqlAlchemySchedulingProblemRepository(session).load_by_school_and_year(
                school_natural_id, academic_year_natural_id,
            )
            validate(current_problem)

            activity_row = _resolve_club_activity(
                session, year_id, fields.special_activity_id, school_natural_id, academic_year_natural_id,
            )
            teacher_row = _resolve_teacher(
                session, year_id, fields.teacher_id, school_natural_id, academic_year_natural_id,
            )
            resource_row = _resolve_resource(
                session, year_id, fields.resource_id, school_natural_id, academic_year_natural_id,
            )
            class_rows = _resolve_class_sections(
                session, year_id, fields.class_section_ids, school_natural_id, academic_year_natural_id,
            )
            slot_rows = _resolve_slots(session, year_id, fields.slots, school_natural_id, academic_year_natural_id)

            block_row = orm.ReservedBlock(
                academic_year_id=year_id, natural_id=reserved_activity_natural_id,
                name=activity_row.name, activity_id=activity_row.id,
                teacher_id=teacher_row.id if teacher_row is not None else None,
                resource_id=resource_row.id if resource_row is not None else None,
                ordinal=_next_reserved_block_ordinal(session, year_id),
            )
            session.add(block_row)
            session.flush()  # obtain block_row.id (surrogate)

            _insert_children(session, year_id, block_row.id, class_rows, slot_rows)
            session.commit()

            return _write_result(
                reserved_activity_natural_id, activity_row, teacher_row, resource_row, class_rows, slot_rows,
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
        reserved_activity_natural_id: str,
        fields: ReservedActivityFields,
        validate: Callable[[SchedulingProblem], None],
    ) -> ReservedActivityWriteResult:
        session = self._session_factory()
        try:
            year_id = resolve_year_id(session, school_natural_id, academic_year_natural_id)
            lock_academic_year(session, year_id)
            reject_if_configuration_locked(session, year_id, school_natural_id, academic_year_natural_id)

            current_problem = SqlAlchemySchedulingProblemRepository(session).load_by_school_and_year(
                school_natural_id, academic_year_natural_id,
            )
            # `validate` (application-owned) already confirms
            # `reserved_activity_natural_id` resolves -- the row lookup
            # below is therefore never expected to miss, but is still
            # checked explicitly rather than trusting the domain-level
            # check alone.
            validate(current_problem)

            block_row = session.execute(
                select(orm.ReservedBlock).where(
                    orm.ReservedBlock.academic_year_id == year_id,
                    orm.ReservedBlock.natural_id == reserved_activity_natural_id,
                )
            ).scalar_one_or_none()
            if block_row is None:
                raise ReservedActivityNotFoundError(
                    school_natural_id, academic_year_natural_id, reserved_activity_natural_id,
                )

            activity_row = _resolve_club_activity(
                session, year_id, fields.special_activity_id, school_natural_id, academic_year_natural_id,
            )
            teacher_row = _resolve_teacher(
                session, year_id, fields.teacher_id, school_natural_id, academic_year_natural_id,
            )
            resource_row = _resolve_resource(
                session, year_id, fields.resource_id, school_natural_id, academic_year_natural_id,
            )
            class_rows = _resolve_class_sections(
                session, year_id, fields.class_section_ids, school_natural_id, academic_year_natural_id,
            )
            slot_rows = _resolve_slots(session, year_id, fields.slots, school_natural_id, academic_year_natural_id)

            session.execute(
                sql_delete(orm.ReservedBlockClassSection).where(
                    orm.ReservedBlockClassSection.reserved_block_id == block_row.id,
                )
            )
            session.execute(
                sql_delete(orm.ReservedBlockSlot).where(orm.ReservedBlockSlot.reserved_block_id == block_row.id)
            )

            block_row.activity_id = activity_row.id
            block_row.name = activity_row.name
            block_row.teacher_id = teacher_row.id if teacher_row is not None else None
            # Full-replacement, matching every other field on this row
            # (Resources B2) -- `resource_id=None` always clears it.
            block_row.resource_id = resource_row.id if resource_row is not None else None
            # natural_id and ordinal are never touched.

            _insert_children(session, year_id, block_row.id, class_rows, slot_rows)
            session.commit()

            return _write_result(
                reserved_activity_natural_id, activity_row, teacher_row, resource_row, class_rows, slot_rows,
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
        reserved_activity_natural_id: str,
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

            block_row = session.execute(
                select(orm.ReservedBlock).where(
                    orm.ReservedBlock.academic_year_id == year_id,
                    orm.ReservedBlock.natural_id == reserved_activity_natural_id,
                )
            ).scalar_one_or_none()
            if block_row is None:
                raise ReservedActivityNotFoundError(
                    school_natural_id, academic_year_natural_id, reserved_activity_natural_id,
                )
            # Its own `class_sections`/`slots` children cascade at the
            # DB level (`ondelete="CASCADE"`); the referenced Special
            # Activity/Teacher/ClassSection/Day/Period rows are never
            # touched (their own FKs from this table are RESTRICT, not
            # CASCADE, and are never even reached here since only the
            # `reserved_block` row itself is deleted).
            session.delete(block_row)
            session.commit()
        except BaseException:
            session.rollback()
            raise
        finally:
            session.close()


def _resolve_club_activity(
    session: Session, year_id: int, special_activity_natural_id: str,
    school_natural_id: str, academic_year_natural_id: str,
) -> orm.Activity:
    row = session.execute(
        select(orm.Activity).where(
            orm.Activity.academic_year_id == year_id,
            orm.Activity.natural_id == special_activity_natural_id,
        )
    ).scalar_one_or_none()
    if row is None:
        raise UnknownReferenceError(
            school_natural_id, academic_year_natural_id, "special_activity", special_activity_natural_id,
        )
    if row.kind != _CLUB:
        raise NonSpecialActivityTargetError(school_natural_id, academic_year_natural_id, special_activity_natural_id)
    return row


def _resolve_teacher(
    session: Session, year_id: int, teacher_natural_id: str | None,
    school_natural_id: str, academic_year_natural_id: str,
) -> orm.Teacher | None:
    if teacher_natural_id is None:
        return None
    row = session.execute(
        select(orm.Teacher).where(
            orm.Teacher.academic_year_id == year_id,
            orm.Teacher.natural_id == teacher_natural_id,
        )
    ).scalar_one_or_none()
    if row is None:
        raise UnknownReferenceError(school_natural_id, academic_year_natural_id, "teacher", teacher_natural_id)
    return row


def _resolve_resource(
    session: Session, year_id: int, resource_natural_id: str | None,
    school_natural_id: str, academic_year_natural_id: str,
) -> orm.Resource | None:
    if resource_natural_id is None:
        return None
    row = session.execute(
        select(orm.Resource).where(
            orm.Resource.academic_year_id == year_id,
            orm.Resource.natural_id == resource_natural_id,
        )
    ).scalar_one_or_none()
    if row is None:
        raise UnknownReferenceError(school_natural_id, academic_year_natural_id, "resource", resource_natural_id)
    return row


def _resolve_class_sections(
    session: Session, year_id: int, class_section_ids: tuple[str, ...],
    school_natural_id: str, academic_year_natural_id: str,
) -> list[orm.ClassSection]:
    rows = session.execute(
        select(orm.ClassSection).where(
            orm.ClassSection.academic_year_id == year_id,
            orm.ClassSection.natural_id.in_(class_section_ids),
        )
    ).scalars().all()
    by_natural = {row.natural_id: row for row in rows}
    for class_id in class_section_ids:
        if class_id not in by_natural:
            raise UnknownReferenceError(school_natural_id, academic_year_natural_id, "class_section", class_id)
    # Canonical order: the referenced ClassSection's own authoritative
    # persisted `ordinal` -- never request order.
    return sorted(by_natural.values(), key=lambda row: row.ordinal)


def _resolve_slots(
    session: Session, year_id: int, slots: tuple[ReservedActivitySlotFields, ...],
    school_natural_id: str, academic_year_natural_id: str,
) -> list[tuple[orm.Day, orm.Period]]:
    day_rows = {
        row.natural_id: row
        for row in session.execute(select(orm.Day).where(orm.Day.academic_year_id == year_id)).scalars()
    }
    period_rows = {
        row.natural_id: row
        for row in session.execute(select(orm.Period).where(orm.Period.academic_year_id == year_id)).scalars()
    }
    resolved: list[tuple[orm.Day, orm.Period]] = []
    for slot in slots:
        day_row = day_rows.get(slot.day_id)
        if day_row is None:
            raise UnknownReferenceError(school_natural_id, academic_year_natural_id, "day", slot.day_id)
        period_row = period_rows.get(slot.period_id)
        if period_row is None:
            raise UnknownReferenceError(school_natural_id, academic_year_natural_id, "period", slot.period_id)
        resolved.append((day_row, period_row))
    # Canonical order: Day.idx then Period.idx -- never request order.
    resolved.sort(key=lambda pair: (pair[0].idx, pair[1].idx))
    return resolved


def _insert_children(
    session: Session, year_id: int, block_id: int,
    class_rows: list[orm.ClassSection], slot_rows: list[tuple[orm.Day, orm.Period]],
) -> None:
    for ordinal, class_row in enumerate(class_rows):
        session.add(orm.ReservedBlockClassSection(
            academic_year_id=year_id, reserved_block_id=block_id, class_section_id=class_row.id, ordinal=ordinal,
        ))
    for ordinal, (day_row, period_row) in enumerate(slot_rows):
        session.add(orm.ReservedBlockSlot(
            academic_year_id=year_id, reserved_block_id=block_id,
            day_id=day_row.id, period_id=period_row.id, ordinal=ordinal,
        ))


def _write_result(
    reserved_activity_natural_id: str,
    activity_row: orm.Activity,
    teacher_row: orm.Teacher | None,
    resource_row: orm.Resource | None,
    class_rows: list[orm.ClassSection],
    slot_rows: list[tuple[orm.Day, orm.Period]],
) -> ReservedActivityWriteResult:
    return ReservedActivityWriteResult(
        id=reserved_activity_natural_id,
        special_activity_id=activity_row.natural_id,
        class_section_ids=tuple(row.natural_id for row in class_rows),
        teacher_id=teacher_row.natural_id if teacher_row is not None else None,
        slots=tuple(
            ReservedActivitySlotFields(day_id=day_row.natural_id, period_id=period_row.natural_id)
            for day_row, period_row in slot_rows
        ),
        resource_id=resource_row.natural_id if resource_row is not None else None,
    )


def _next_reserved_block_ordinal(session: Session, year_id: int) -> int:
    """`ReservedBlock`'s own ordinal sequence -- never shared with any
    other table (unlike `Activity`'s shared ORDINARY+CLUB sequence)."""
    max_ordinal = session.execute(
        select(func.max(orm.ReservedBlock.ordinal)).where(orm.ReservedBlock.academic_year_id == year_id)
    ).scalar()
    return 0 if max_ordinal is None else max_ordinal + 1
