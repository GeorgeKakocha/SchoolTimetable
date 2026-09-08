"""SQLAlchemy adapter implementing `application.ports.
TeachingAssignmentRepository` (Phase 3C.2, `docs/DECISIONS.md` #34,
#36) -- the first production configuration *write* path this codebase
has ever had (`SchedulingProblemRepository` stays read-only).

Session-factory-backed, exactly like `SqlAlchemyScheduleVersionRepository`:
every public method opens and closes its own short `Session`. Each
method is one atomic transaction that, in this exact order: (1) resolves
the `AcademicYear` surrogate ID; (2) acquires a `SELECT ... FOR UPDATE`
row lock on that `AcademicYear` -- the same lock `persist_initial_version`
takes immediately before persisting a generated schedule (Owner
Decision #36) -- serializing every Phase 3C.2 configuration write for
one year against every other one, and against an in-flight generation's
final persist step; (3) authoritatively rechecks, under that lock, that
no `Schedule` exists for this year (`docs/DECISIONS.md` #35), raising
`ConfigurationLockedError` regardless of what an earlier, un-locked
caller precheck found; (4) reloads the current authoritative
`SchedulingProblem` under the same lock and invokes the caller-supplied
`validate` callback against it -- never against a stale, pre-lock
snapshot; (5) only if `validate` does not raise, performs the write and
commits, still holding the lock until that commit. Any failure rolls
back the whole transaction -- no partial mutation ever survives.

The lock is held only for the duration of one of these short
transactions -- never across a CP-SAT solve, and never while a caller
waits between two separate requests.
"""
from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from school_timetable.application.errors import ConfigurationLockedError, SchedulingProblemNotFoundError
from school_timetable.domain.problem import SchedulingProblem
from school_timetable.domain.requirements import BlockPolicyMode
from school_timetable.persistence import models as orm
from school_timetable.persistence.problem_repository import SqlAlchemySchedulingProblemRepository

_PLAIN_BLOCK_MODE = BlockPolicyMode.FLEXIBLE.value


class SqlAlchemyTeachingAssignmentRepository:
    """Implements `application.ports.TeachingAssignmentRepository`
    structurally (a `Protocol` -- no inheritance needed)."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def create(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        natural_id: str,
        teacher_id: str,
        participant_group_id: str,
        activity_id: str,
        weekly_periods: int,
        validate: Callable[[SchedulingProblem], None],
    ) -> None:
        session = self._session_factory()
        try:
            year_id = _resolve_year_id(session, school_natural_id, academic_year_natural_id)
            _lock_academic_year(session, year_id)
            _reject_if_configuration_locked(session, year_id, school_natural_id, academic_year_natural_id)

            current_problem = SqlAlchemySchedulingProblemRepository(session).load_by_school_and_year(
                school_natural_id, academic_year_natural_id,
            )
            validate(current_problem)

            teacher_ids = _natural_to_surrogate(session, orm.Teacher, year_id)
            activity_ids = _natural_to_surrogate(session, orm.Activity, year_id)
            group_ids = _natural_to_surrogate(session, orm.ParticipantGroup, year_id)

            session.add(orm.TeachingRequirement(
                academic_year_id=year_id,
                natural_id=natural_id,
                teacher_id=teacher_ids[teacher_id],
                activity_id=activity_ids[activity_id],
                participant_group_id=group_ids[participant_group_id],
                weekly_periods=weekly_periods,
                block_mode=_PLAIN_BLOCK_MODE,
                block_sizes=[],
                min_distinct_days=None,
                max_periods_per_day=None,
                resource_id=None,
                split_group_id=None,
                ordinal=_next_ordinal(session, year_id),
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
        teacher_id: str,
        participant_group_id: str,
        activity_id: str,
        weekly_periods: int,
        validate: Callable[[SchedulingProblem], None],
    ) -> None:
        session = self._session_factory()
        try:
            year_id = _resolve_year_id(session, school_natural_id, academic_year_natural_id)
            _lock_academic_year(session, year_id)
            _reject_if_configuration_locked(session, year_id, school_natural_id, academic_year_natural_id)

            current_problem = SqlAlchemySchedulingProblemRepository(session).load_by_school_and_year(
                school_natural_id, academic_year_natural_id,
            )
            # `validate` (application-owned) already confirms `natural_id`
            # exists and is plain -- the row lookup below is therefore
            # never expected to miss.
            validate(current_problem)

            teacher_ids = _natural_to_surrogate(session, orm.Teacher, year_id)
            activity_ids = _natural_to_surrogate(session, orm.Activity, year_id)
            group_ids = _natural_to_surrogate(session, orm.ParticipantGroup, year_id)

            row = session.execute(
                select(orm.TeachingRequirement).where(
                    orm.TeachingRequirement.academic_year_id == year_id,
                    orm.TeachingRequirement.natural_id == natural_id,
                )
            ).scalar_one()
            row.teacher_id = teacher_ids[teacher_id]
            row.activity_id = activity_ids[activity_id]
            row.participant_group_id = group_ids[participant_group_id]
            row.weekly_periods = weekly_periods
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
            year_id = _resolve_year_id(session, school_natural_id, academic_year_natural_id)
            _lock_academic_year(session, year_id)
            _reject_if_configuration_locked(session, year_id, school_natural_id, academic_year_natural_id)

            current_problem = SqlAlchemySchedulingProblemRepository(session).load_by_school_and_year(
                school_natural_id, academic_year_natural_id,
            )
            validate(current_problem)

            row = session.execute(
                select(orm.TeachingRequirement).where(
                    orm.TeachingRequirement.academic_year_id == year_id,
                    orm.TeachingRequirement.natural_id == natural_id,
                )
            ).scalar_one()
            session.delete(row)
            session.commit()
        except BaseException:
            session.rollback()
            raise
        finally:
            session.close()


def _resolve_year_id(session: Session, school_natural_id: str, academic_year_natural_id: str) -> int:
    year_id = session.execute(
        select(orm.AcademicYear.id)
        .join(orm.School, orm.School.id == orm.AcademicYear.school_id)
        .where(
            orm.School.natural_id == school_natural_id,
            orm.AcademicYear.natural_id == academic_year_natural_id,
        )
    ).scalar_one_or_none()
    if year_id is None:
        raise SchedulingProblemNotFoundError(school_natural_id, academic_year_natural_id)
    return year_id


def _lock_academic_year(session: Session, year_id: int) -> None:
    """Owner Decision #36: a short, exclusive row lock on this
    `AcademicYear`, held only for the remainder of the caller's
    transaction -- shared with `persist_initial_version`'s own
    pre-persist lock (`schedule_repository.py`), serializing every
    Phase 3C.2 configuration write against every other one and against
    an in-flight generation's final persist step."""
    session.execute(select(orm.AcademicYear.id).where(orm.AcademicYear.id == year_id).with_for_update())


def _reject_if_configuration_locked(
    session: Session, year_id: int, school_natural_id: str, academic_year_natural_id: str,
) -> None:
    """Authoritative Decision #35 recheck -- performed *after* acquiring
    the Decision #36 lock, never relying solely on an earlier, un-locked
    caller precheck (`TeachingAssignmentService`'s own fast-fail check
    is exactly that: a precheck, not the concurrency guarantee)."""
    schedule_exists = session.execute(
        select(orm.Schedule.id).where(orm.Schedule.academic_year_id == year_id)
    ).scalar_one_or_none() is not None
    if schedule_exists:
        session.rollback()
        raise ConfigurationLockedError(school_natural_id, academic_year_natural_id)


def _natural_to_surrogate(session: Session, model: type, year_id: int) -> dict[str, int]:
    rows = session.execute(select(model).where(model.academic_year_id == year_id)).scalars().all()
    return {row.natural_id: row.id for row in rows}


def _next_ordinal(session: Session, year_id: int) -> int:
    max_ordinal = session.execute(
        select(func.max(orm.TeachingRequirement.ordinal)).where(
            orm.TeachingRequirement.academic_year_id == year_id
        )
    ).scalar()
    return 0 if max_ordinal is None else max_ordinal + 1
