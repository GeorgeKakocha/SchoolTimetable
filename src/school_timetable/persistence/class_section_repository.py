"""SQLAlchemy adapter implementing `application.ports.
ClassSectionRepository` (Real-School Setup MVP Slice C).

Session-factory-backed, exactly like `SqlAlchemyTeacherRepository`:
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
shared, unchanged, with `SqlAlchemyTeacherRepository`/
`SqlAlchemyTeachingAssignmentRepository` via `configuration_write_lock.py`.

The canonical `WHOLE_CLASS` `ParticipantGroup` is resolved only via
`class_section_rules.resolve_canonical_whole_class_group` (role +
exact-one-class membership against the freshly-reloaded domain
problem) -- never by name, ID prefix, or database row order.
"""
from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from school_timetable.application import class_section_rules as rules
from school_timetable.application.errors import ClassSectionNotFoundError
from school_timetable.domain.problem import SchedulingProblem
from school_timetable.persistence import models as orm
from school_timetable.persistence.configuration_write_lock import (
    lock_academic_year,
    reject_if_configuration_locked,
    resolve_year_id,
)
from school_timetable.persistence.problem_repository import SqlAlchemySchedulingProblemRepository


class SqlAlchemyClassSectionRepository:
    """Implements `application.ports.ClassSectionRepository`
    structurally (a `Protocol` -- no inheritance needed)."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def create(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        class_natural_id: str,
        canonical_group_natural_id: str,
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

            class_row = orm.ClassSection(
                academic_year_id=year_id, natural_id=class_natural_id,
                name=name, ordinal=_next_class_ordinal(session, year_id),
            )
            session.add(class_row)
            session.flush()  # obtain class_row.id (surrogate)

            group_row = orm.ParticipantGroup(
                academic_year_id=year_id, natural_id=canonical_group_natural_id,
                name=name, role="WHOLE_CLASS", ordinal=_next_group_ordinal(session, year_id),
            )
            session.add(group_row)
            session.flush()  # obtain group_row.id (surrogate)

            session.add(orm.ParticipantGroupClassSection(
                academic_year_id=year_id, participant_group_id=group_row.id,
                class_section_id=class_row.id, ordinal=0,
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
        class_natural_id: str,
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
            # `validate` (application-owned) already confirms `class_natural_id`
            # exists and the canonical WHOLE_CLASS invariant holds -- the
            # row lookups below are therefore never expected to miss, but
            # are still checked explicitly rather than trusting a raw
            # SQLAlchemy `NoResultFound`.
            validate(current_problem)

            class_row = session.execute(
                select(orm.ClassSection).where(
                    orm.ClassSection.academic_year_id == year_id,
                    orm.ClassSection.natural_id == class_natural_id,
                )
            ).scalar_one_or_none()
            if class_row is None:
                raise ClassSectionNotFoundError(school_natural_id, academic_year_natural_id, class_natural_id)

            # Resolve the canonical group from the DOMAIN problem (role +
            # exact-one-class membership), never by name or DB row order.
            canonical_group = rules.resolve_canonical_whole_class_group(current_problem, class_natural_id)
            group_row = session.execute(
                select(orm.ParticipantGroup).where(
                    orm.ParticipantGroup.academic_year_id == year_id,
                    orm.ParticipantGroup.natural_id == canonical_group.id,
                )
            ).scalar_one_or_none()
            if group_row is None:
                # Domain-resolved the group but its ORM row is missing --
                # an internal invariant defect, never a client-facing
                # not-found/in-use outcome.
                raise rules.CanonicalWholeClassGroupInvariantError(class_natural_id, ())

            class_row.name = name
            group_row.name = name
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
        class_natural_id: str,
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
            # `validate` re-confirms existence, the canonical invariant,
            # AND that no current configuration reference blocks the
            # delete (`ClassSectionInUseError`).
            validate(current_problem)

            class_row = session.execute(
                select(orm.ClassSection).where(
                    orm.ClassSection.academic_year_id == year_id,
                    orm.ClassSection.natural_id == class_natural_id,
                )
            ).scalar_one_or_none()
            if class_row is None:
                raise ClassSectionNotFoundError(school_natural_id, academic_year_natural_id, class_natural_id)

            canonical_group = rules.resolve_canonical_whole_class_group(current_problem, class_natural_id)
            group_row = session.execute(
                select(orm.ParticipantGroup).where(
                    orm.ParticipantGroup.academic_year_id == year_id,
                    orm.ParticipantGroup.natural_id == canonical_group.id,
                )
            ).scalar_one_or_none()
            if group_row is None:
                raise rules.CanonicalWholeClassGroupInvariantError(class_natural_id, ())

            membership_row = session.execute(
                select(orm.ParticipantGroupClassSection).where(
                    orm.ParticipantGroupClassSection.academic_year_id == year_id,
                    orm.ParticipantGroupClassSection.participant_group_id == group_row.id,
                    orm.ParticipantGroupClassSection.class_section_id == class_row.id,
                )
            ).scalar_one_or_none()

            # Safe deletion order: the membership FK to class_section is
            # RESTRICT, so the owned membership (and then the owned
            # canonical group) must be removed before the ClassSection
            # itself -- never SUBGROUP/MERGED_CLASSES memberships,
            # TeachingRequirements, or ReservedBlocks (already proven
            # absent by `validate` above).
            if membership_row is not None:
                session.delete(membership_row)
                session.flush()
            session.delete(group_row)
            session.flush()
            session.delete(class_row)
            session.commit()
        except BaseException:
            session.rollback()
            raise
        finally:
            session.close()


def _next_class_ordinal(session: Session, year_id: int) -> int:
    max_ordinal = session.execute(
        select(func.max(orm.ClassSection.ordinal)).where(orm.ClassSection.academic_year_id == year_id)
    ).scalar()
    return 0 if max_ordinal is None else max_ordinal + 1


def _next_group_ordinal(session: Session, year_id: int) -> int:
    max_ordinal = session.execute(
        select(func.max(orm.ParticipantGroup.ordinal)).where(orm.ParticipantGroup.academic_year_id == year_id)
    ).scalar()
    return 0 if max_ordinal is None else max_ordinal + 1
