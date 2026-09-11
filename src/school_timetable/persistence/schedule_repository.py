"""SQLAlchemy adapter implementing `application.ports.
ScheduleVersionRepository` (Phase 3A3.2, `docs/DECISIONS.md` #31).

Session-factory-backed: every public method opens and closes its own
short `Session` (never constructed with an already-open `Session`) --
the same connection-lifetime guarantee `SessionFactorySchedulingProblemRepository`
in `problem_repository.py` provides, required so a future
`GenerateScheduleService` (Phase 3A3.3) never holds a database
connection open across a CP-SAT solve (Owner Decision 4).

`persist_initial_version` writes `Schedule` + its first `ScheduleVersion`
+ `ScheduleEntry` rows + the active-version pointer inside one atomic
transaction, exactly the three-step sequence Decision #31 locks: (1)
insert `Schedule` with `active_version_id` `NULL`; (2) insert
`ScheduleVersion` 1; (3) insert `ScheduleEntry` rows (`ordinal` from
`enumerate`), then update `Schedule.active_version_id`. Any failure
rolls back the whole thing -- no partial `Schedule`/`ScheduleVersion`/
`ScheduleEntry` row ever survives. Exactly one integrity violation is
ever translated: a violation of the `uq_schedule_academic_year_id`
constraint by name (confirmed via psycopg/PostgreSQL structured
diagnostics, `exc.orig.diag.constraint_name` -- never by parsing error
text, never by catching every `IntegrityError`) becomes
`application.errors.ScheduleAlreadyExistsError` -- raised as a clean
application exception only after control has fully left the
`except IntegrityError` block (not via `raise ... from exc`), so its
`__cause__` and `__context__` are both always `None`: the
application-owned conflict error never exposes the underlying
SQLAlchemy/psycopg exception through either normal exception-chaining
attribute, only the natural school/year IDs the caller already
supplied. Every other integrity violation propagates as itself -- a
genuine defect, never silently reinterpreted.

`persist_initial_version` additionally implements Owner Decision #36
(Phase 3C.2): immediately before the insert sequence above, it acquires
a `SELECT ... FOR UPDATE` row lock on the `AcademicYear` (the same lock
`SqlAlchemyTeachingAssignmentRepository`'s config-write transactions
take), reloads the current authoritative `SchedulingProblem` under that
lock, and compares it against the `problem` the solver actually solved
-- if a configuration write committed in the DB-free window between
`GenerateScheduleService` loading `problem` and this call, the two
differ and `ConfigurationChangedDuringGenerationError` is raised
instead of persisting, with no `Schedule`/`ScheduleVersion`/
`ScheduleEntry` row ever created. The lock is held only for this short
transaction -- never across the CP-SAT solve, which has already
finished by the time this method is even called.

`persist_edited_version` (manual-editing backend persistence slice) is
the write-side sibling for every *subsequent* version: it reuses the
exact same Owner-Decision-#36 `AcademicYear` row lock, then compares the
actual current active version's `version_number` against the caller's
`base_version_number` (never a surrogate ID) -- a mismatch means someone
else's edit/re-optimization was promoted first, and raises
`StaleScheduleVersionError` with zero rows written. On a match, it
inserts exactly one new `ScheduleVersion` (`parent_version_id` set to
the previous active version's surrogate ID, `version_number` one past
`MAX(version_number)` for this `Schedule` -- not just `active + 1` --
so a future non-linear history can never collide even though this slice
only ever produces a linear chain), its own `ScheduleEntry` rows (same
shape as `persist_initial_version`'s), and its own `LockedOccurrence`
rows from the candidate `Schedule.locked_occurrences`, then atomically
repoints `Schedule.active_version_id`. No prior version's rows are ever
touched.

`get_active_schedule` re-derives each `ScheduleEntry`'s
`activity_id`/`teacher_id`/`participant_group_id`/`resource_id`/
`class_sections` by joining back to the referenced configuration
(`TeachingRequirement`/`ReservedBlock`/`ParticipantGroup`), reusing
`SqlAlchemySchedulingProblemRepository` against its own `Session` to do
so, exactly as `docs/DECISIONS.md` #31 requires (`schedule_entry`
deliberately does not store those columns). It returns `None` only when
no `Schedule` row exists at all for a valid school/year -- a `Schedule`
row that exists but has `active_version_id IS NULL` (or whose active
pointer does not resolve to an existing `schedule_version` row) is
corrupt persisted state, never "no schedule yet", and raises
`CorruptScheduleStateError` instead of silently returning `None` or
being reinterpreted as a conflict.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from school_timetable.application.errors import (
    ConfigurationChangedDuringGenerationError,
    ScheduleAlreadyExistsError,
    SchedulingProblemNotFoundError,
    StaleScheduleVersionError,
)
from school_timetable.application.schedule_models import ActiveScheduleVersion
from school_timetable.domain.problem import SchedulingProblem
from school_timetable.domain.result import EntrySource, ScheduleEntry, SolverStatus
from school_timetable.domain.schedule import OccurrenceKey, Schedule
from school_timetable.persistence import mappers as mp
from school_timetable.persistence import models as orm
from school_timetable.persistence.problem_repository import SqlAlchemySchedulingProblemRepository

_ACTIVE_SCHEDULE_CONSTRAINT = "uq_schedule_academic_year_id"


class CorruptScheduleStateError(RuntimeError):
    """Internal persistence defect -- a persisted `Schedule` row exists
    outside `persist_initial_version`'s own atomic transaction but its
    `active_version_id` is `NULL`, or that pointer does not resolve to
    an existing `schedule_version` row (Decision #31's creation
    sequence guarantees this never happens for correctly-written data).
    Never a public/application-level outcome, never translated to
    `None` or `ScheduleAlreadyExistsError` -- the read adapter must fail
    loudly rather than silently reinterpret corrupt state. Carries only
    the natural school/year IDs already supplied by the caller, never a
    persistence surrogate ID or SQL text."""


class SqlAlchemyScheduleVersionRepository:
    """Implements `application.ports.ScheduleVersionRepository`
    structurally (a `Protocol` -- no inheritance needed)."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def get_active_schedule(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
    ) -> ActiveScheduleVersion | None:
        session = self._session_factory()
        try:
            year_id = _resolve_year_id(session, school_natural_id, academic_year_natural_id)

            schedule_row = session.execute(
                select(orm.Schedule).where(orm.Schedule.academic_year_id == year_id)
            ).scalar_one_or_none()
            if schedule_row is None:
                return None
            if schedule_row.active_version_id is None:
                # `active_version_id` is only ever NULL inside
                # `persist_initial_version`'s own atomic transaction (see
                # Decision #31's creation sequence) -- a *persisted*
                # `Schedule` row with a NULL pointer is corrupt state, not
                # "no schedule yet". Never silently reinterpreted as
                # `None` or as a conflict.
                raise CorruptScheduleStateError(
                    f"schedule exists for school={school_natural_id!r}, "
                    f"academic_year={academic_year_natural_id!r} but has no "
                    "active version (active_version_id is NULL outside the "
                    "atomic creation transaction)"
                )

            version_row = session.get(orm.ScheduleVersion, schedule_row.active_version_id)
            if version_row is None:
                raise CorruptScheduleStateError(
                    f"schedule's active version pointer for school="
                    f"{school_natural_id!r}, academic_year={academic_year_natural_id!r} "
                    "does not reference an existing schedule_version row"
                )

            entry_rows = list(
                session.execute(
                    select(orm.ScheduleEntry).where(orm.ScheduleEntry.schedule_version_id == version_row.id)
                ).scalars()
            )
            locked_rows = list(
                session.execute(
                    select(orm.LockedOccurrence).where(
                        orm.LockedOccurrence.schedule_version_id == version_row.id
                    )
                ).scalars()
            )

            problem = SqlAlchemySchedulingProblemRepository(session).load_by_school_and_year(
                school_natural_id, academic_year_natural_id
            )
            entries, locked_occurrences = _resolve_entries(session, year_id, problem, entry_rows, locked_rows)

            return ActiveScheduleVersion(
                version_number=version_row.version_number,
                solver_status=SolverStatus(version_row.solver_status),
                total_soft_penalty=version_row.total_soft_penalty,
                wall_time_seconds=version_row.wall_time_seconds,
                random_seed=version_row.random_seed,
                created_at=version_row.created_at,
                entries=entries,
                locked_occurrences=locked_occurrences,
            )
        finally:
            session.close()

    def persist_initial_version(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        problem: SchedulingProblem,
        entries: tuple[ScheduleEntry, ...],
        solver_status: SolverStatus,
        total_soft_penalty: int,
        wall_time_seconds: float,
        random_seed: int | None,
    ) -> ActiveScheduleVersion:
        session = self._session_factory()
        try:
            year_id = _resolve_year_id(session, school_natural_id, academic_year_natural_id)

            # Owner Decision #36: acquire a short, exclusive row lock on
            # this AcademicYear -- shared with every Phase 3C.2
            # configuration-write transaction (see
            # `SqlAlchemyTeachingAssignmentRepository`) -- then, while
            # holding it, reload the CURRENT authoritative configuration
            # and compare it against `problem` (the exact configuration
            # the solver actually solved). The lock is acquired only
            # here, immediately before this short persist transaction --
            # never across the solve that already finished before this
            # method was even called.
            session.execute(
                select(orm.AcademicYear.id).where(orm.AcademicYear.id == year_id).with_for_update()
            )
            current_problem = SqlAlchemySchedulingProblemRepository(session).load_by_school_and_year(
                school_natural_id, academic_year_natural_id,
            )
            if current_problem != problem:
                session.rollback()
                raise ConfigurationChangedDuringGenerationError(school_natural_id, academic_year_natural_id)

            day_ids = _natural_to_surrogate(session, orm.Day, year_id)
            period_ids = _natural_to_surrogate(session, orm.Period, year_id)
            requirement_ids = _natural_to_surrogate(session, orm.TeachingRequirement, year_id)
            reserved_block_ids = _natural_to_surrogate(session, orm.ReservedBlock, year_id)

            try:
                schedule_row = orm.Schedule(academic_year_id=year_id, active_version_id=None)
                session.add(schedule_row)
                session.flush()

                version_row = orm.ScheduleVersion(
                    academic_year_id=year_id,
                    schedule_id=schedule_row.id,
                    version_number=1,
                    parent_version_id=None,
                    solver_status=solver_status.value,
                    total_soft_penalty=total_soft_penalty,
                    wall_time_seconds=wall_time_seconds,
                    random_seed=random_seed,
                )
                session.add(version_row)
                session.flush()

                for ordinal, entry in enumerate(entries):
                    is_requirement = entry.source == EntrySource.REQUIREMENT
                    session.add(orm.ScheduleEntry(
                        academic_year_id=year_id,
                        schedule_version_id=version_row.id,
                        ordinal=ordinal,
                        source=entry.source.value,
                        day_id=day_ids[entry.day_id],
                        period_id=period_ids[entry.period_id],
                        teaching_requirement_id=(
                            requirement_ids[entry.requirement_id] if is_requirement else None
                        ),
                        reserved_block_id=(
                            None if is_requirement else reserved_block_ids[entry.reserved_block_id]
                        ),
                    ))
                session.flush()

                schedule_row.active_version_id = version_row.id
                session.flush()

                session.commit()
            except IntegrityError as exc:
                session.rollback()
                if not _violates(exc, _ACTIVE_SCHEDULE_CONSTRAINT):
                    raise
                # Read the structured diagnostic *while still inside* this
                # except block, then let the block end normally rather
                # than raising here -- raising `ScheduleAlreadyExistsError`
                # only after we have fully left the `except IntegrityError`
                # handler means Python never sets its `__context__` (no
                # exception is being handled at that point) and we never
                # pass `from exc`, so `__cause__` stays `None` too. The
                # application-owned conflict error must never expose the
                # underlying SQLAlchemy/psycopg exception through either
                # normal exception-chaining attribute -- only the natural
                # school/year IDs the caller already supplied.
                is_conflict = True
            except BaseException:
                session.rollback()
                raise
            else:
                is_conflict = False

            if is_conflict:
                raise ScheduleAlreadyExistsError(school_natural_id, academic_year_natural_id)

            return ActiveScheduleVersion(
                version_number=version_row.version_number,
                solver_status=solver_status,
                total_soft_penalty=total_soft_penalty,
                wall_time_seconds=wall_time_seconds,
                random_seed=random_seed,
                created_at=version_row.created_at,
                entries=entries,
                locked_occurrences=frozenset(),
            )
        finally:
            session.close()

    def persist_edited_version(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        base_version_number: int,
        candidate: Schedule,
        solver_status: SolverStatus,
        total_soft_penalty: int,
        wall_time_seconds: float,
        random_seed: int | None,
    ) -> ActiveScheduleVersion:
        session = self._session_factory()
        try:
            year_id = _resolve_year_id(session, school_natural_id, academic_year_natural_id)

            # Owner Decision #36: the same short, exclusive AcademicYear
            # row lock `persist_initial_version` takes, held only for
            # this short transaction.
            session.execute(
                select(orm.AcademicYear.id).where(orm.AcademicYear.id == year_id).with_for_update()
            )

            schedule_row = session.execute(
                select(orm.Schedule).where(orm.Schedule.academic_year_id == year_id)
            ).scalar_one_or_none()
            if schedule_row is None or schedule_row.active_version_id is None:
                session.rollback()
                raise CorruptScheduleStateError(
                    f"no active schedule exists for school={school_natural_id!r}, "
                    f"academic_year={academic_year_natural_id!r} to edit -- "
                    "persist_edited_version requires an existing active ScheduleVersion "
                    "(use persist_initial_version to create the first one)"
                )

            active_version_row = session.get(orm.ScheduleVersion, schedule_row.active_version_id)
            if active_version_row is None:
                session.rollback()
                raise CorruptScheduleStateError(
                    f"schedule's active version pointer for school={school_natural_id!r}, "
                    f"academic_year={academic_year_natural_id!r} does not reference an "
                    "existing schedule_version row"
                )

            if active_version_row.version_number != base_version_number:
                session.rollback()
                raise StaleScheduleVersionError(
                    school_natural_id,
                    academic_year_natural_id,
                    base_version_number,
                    active_version_row.version_number,
                )

            # One past the highest version_number for this Schedule --
            # not just `active_version_row.version_number + 1` -- so a
            # future non-linear history can never collide, even though
            # this slice only ever produces a linear chain from the
            # active version.
            max_version_number = session.execute(
                select(func.max(orm.ScheduleVersion.version_number)).where(
                    orm.ScheduleVersion.schedule_id == schedule_row.id
                )
            ).scalar_one()
            next_version_number = max_version_number + 1

            day_ids = _natural_to_surrogate(session, orm.Day, year_id)
            period_ids = _natural_to_surrogate(session, orm.Period, year_id)
            requirement_ids = _natural_to_surrogate(session, orm.TeachingRequirement, year_id)
            reserved_block_ids = _natural_to_surrogate(session, orm.ReservedBlock, year_id)

            try:
                new_version_row = orm.ScheduleVersion(
                    academic_year_id=year_id,
                    schedule_id=schedule_row.id,
                    version_number=next_version_number,
                    parent_version_id=active_version_row.id,
                    solver_status=solver_status.value,
                    total_soft_penalty=total_soft_penalty,
                    wall_time_seconds=wall_time_seconds,
                    random_seed=random_seed,
                )
                session.add(new_version_row)
                session.flush()

                for ordinal, entry in enumerate(candidate.entries):
                    is_requirement = entry.source == EntrySource.REQUIREMENT
                    session.add(orm.ScheduleEntry(
                        academic_year_id=year_id,
                        schedule_version_id=new_version_row.id,
                        ordinal=ordinal,
                        source=entry.source.value,
                        day_id=day_ids[entry.day_id],
                        period_id=period_ids[entry.period_id],
                        teaching_requirement_id=(
                            requirement_ids[entry.requirement_id] if is_requirement else None
                        ),
                        reserved_block_id=(
                            None if is_requirement else reserved_block_ids[entry.reserved_block_id]
                        ),
                    ))
                session.flush()

                for key in candidate.locked_occurrences:
                    session.add(orm.LockedOccurrence(
                        academic_year_id=year_id,
                        schedule_version_id=new_version_row.id,
                        teaching_requirement_id=requirement_ids[key.requirement_id],
                        day_id=day_ids[key.day_id],
                        anchor_period_id=period_ids[key.anchor_period_id],
                    ))
                session.flush()

                schedule_row.active_version_id = new_version_row.id
                session.flush()

                session.commit()
            except BaseException:
                session.rollback()
                raise

            return ActiveScheduleVersion(
                version_number=next_version_number,
                solver_status=solver_status,
                total_soft_penalty=total_soft_penalty,
                wall_time_seconds=wall_time_seconds,
                random_seed=random_seed,
                created_at=new_version_row.created_at,
                entries=candidate.entries,
                locked_occurrences=candidate.locked_occurrences,
            )
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


def _natural_to_surrogate(session: Session, model: type, year_id: int) -> dict[str, int]:
    rows = session.execute(select(model).where(model.academic_year_id == year_id)).scalars().all()
    return {row.natural_id: row.id for row in rows}


def _resolve_entries(
    session: Session,
    year_id: int,
    problem: SchedulingProblem,
    entry_rows: Sequence[orm.ScheduleEntry],
    locked_rows: Sequence[orm.LockedOccurrence],
) -> tuple[tuple[ScheduleEntry, ...], frozenset[OccurrenceKey]]:
    requirements_by_natural_id = {r.id: r for r in problem.teaching_requirements}
    reserved_blocks_by_natural_id = {b.id: b for b in problem.reserved_blocks}
    participant_groups_by_natural_id = {g.id: g for g in problem.participant_groups}

    day_rows = session.execute(select(orm.Day).where(orm.Day.academic_year_id == year_id)).scalars().all()
    period_rows = session.execute(select(orm.Period).where(orm.Period.academic_year_id == year_id)).scalars().all()
    requirement_rows = session.execute(
        select(orm.TeachingRequirement).where(orm.TeachingRequirement.academic_year_id == year_id)
    ).scalars().all()
    reserved_block_rows = session.execute(
        select(orm.ReservedBlock).where(orm.ReservedBlock.academic_year_id == year_id)
    ).scalars().all()

    lookup = mp.NaturalIdLookup.build(
        days=day_rows,
        periods=period_rows,
        teaching_requirements=requirement_rows,
        reserved_blocks=reserved_block_rows,
    )

    entries = tuple(
        mp.schedule_entry_to_domain(
            row,
            lookup,
            requirements_by_natural_id,
            reserved_blocks_by_natural_id,
            participant_groups_by_natural_id,
        )
        for row in sorted(entry_rows, key=lambda r: r.ordinal)
    )
    locked_occurrences = frozenset(mp.locked_occurrence_to_domain(row, lookup) for row in locked_rows)
    return entries, locked_occurrences


def _violates(exc: IntegrityError, constraint_name: str) -> bool:
    diag = getattr(exc.orig, "diag", None)
    return getattr(diag, "constraint_name", None) == constraint_name
