"""Repository ports (Phase 3A2.3, extended Phase 3A3.2, Phase 3C.2):
interfaces `application/` defines and `persistence/` adapters
implement, per the locked ports-and-adapters direction
(`docs/ARCHITECTURE.md`, `docs/DECISIONS.md` #20).

Each `typing.Protocol`, not an ABC -- no inheritance requirement; a
concrete adapter satisfies one purely by matching its method
signatures. `SchedulingProblemRepository` (Phase 3A2.3) loads one
school's one academic year's full configuration, and stays read-only.
`ScheduleVersionRepository` (Phase 3A3.2, `docs/DECISIONS.md` #31) reads
and creates the one canonical generated `Schedule`'s versions.
`TeachingAssignmentRepository` (Phase 3C.2, `docs/DECISIONS.md` #34,
#36) is the first genuine configuration *write* port -- narrowly scoped
to plain `TeachingRequirement` create/update/delete, never generic
configuration CRUD (Decision #12). `TeacherRepository` (Real-School
Setup MVP Slice B) is the second, narrowly scoped to `Teacher`
create/update/delete. `ClassSectionRepository` (Real-School Setup MVP
Slice C) is the third, narrowly scoped to the atomic `ClassSection` +
canonical `WHOLE_CLASS` `ParticipantGroup` aggregate (Owner Decision
#33) create/update/delete. `ActivityRepository` (Real-School Setup MVP
Slice D) is the fourth, narrowly scoped to the user-facing "Subject"
write surface -- `Activity(kind=ORDINARY)` create/update/delete only,
never `CLUB` activities and never a `kind` mutation; this is not a
generic Activity CRUD port and never will be one for Club management.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from school_timetable.application.schedule_models import ActiveScheduleVersion
from school_timetable.domain.problem import SchedulingProblem
from school_timetable.domain.result import ScheduleEntry, SolverStatus


class SchedulingProblemRepository(Protocol):
    def load_by_school_and_year(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
    ) -> SchedulingProblem:
        """Load the complete persisted scheduling configuration for one
        school's one academic year, reconstructed as a frozen
        `SchedulingProblem` with exact tuple order and no persistence
        surrogate ID anywhere in the result.

        Raises `school_timetable.application.errors.
        SchedulingProblemNotFoundError` identically whether
        `school_natural_id` itself is unknown or it is known but
        `academic_year_natural_id` is not -- both are the same
        "this configuration does not exist" outcome to the caller.
        """
        ...


class ScheduleVersionRepository(Protocol):
    """Schedule persistence port (Phase 3A3.2, Decision #31 -- named for
    the operation it actually performs, not a generic
    `ScheduleRepository`). No generic CRUD (`delete`/`update_version`/
    `list_all_versions`/scenario methods) -- none is needed until a
    concrete future use case scopes it (Decision #12)."""

    def get_active_schedule(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
    ) -> ActiveScheduleVersion | None:
        """The active `ScheduleVersion` for this school/year, or `None`
        if no `Schedule` has been generated yet -- that is an expected,
        ordinary outcome, not an error.

        Raises `school_timetable.application.errors.
        SchedulingProblemNotFoundError` (same as `SchedulingProblemRepository`)
        if the school/year itself does not resolve -- distinct from "no
        schedule generated yet for a real school/year", which returns
        `None`.
        """
        ...

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
        """Creates `Schedule` + its first `ScheduleVersion`
        (`version_number=1`, `parent_version_id=None`) and marks it
        active, atomically -- never partially.

        `problem` is the exact `SchedulingProblem` the solver actually
        solved (Owner Decision #36): immediately before persisting,
        this method acquires a short row lock on the `AcademicYear`,
        reloads the *current* authoritative configuration under that
        lock, and compares it against `problem`. If a configuration
        write committed in the gap between the caller's original load
        and this recheck, the two differ and this method raises
        `school_timetable.application.errors.
        ConfigurationChangedDuringGenerationError` instead of
        persisting -- no `Schedule`/`ScheduleVersion`/`ScheduleEntry`
        row is created in that case, and the caller may simply retry
        generation. The lock is held only for this short persist
        transaction, never across the solve that already happened
        before this call.

        Raises `school_timetable.application.errors.
        ScheduleAlreadyExistsError` if a `Schedule` already exists for
        this school/year (Owner Decision 2), including the losing side
        of a concurrent double-generate race, checked both by an
        earlier caller precheck and authoritatively, under the same
        lock, by this method itself. Raises
        `SchedulingProblemNotFoundError` if the school/year itself does
        not resolve.
        """
        ...


class TeachingAssignmentRepository(Protocol):
    """Phase 3C.2 configuration *write* port (`docs/DECISIONS.md` #34,
    #36) -- narrowly scoped to plain `TeachingRequirement`
    create/update/delete, never generic configuration CRUD (Decision
    #12); `SchedulingProblemRepository` stays read-only.

    Each method is one short, atomic transaction that: resolves natural
    IDs to surrogates scoped to the requested `AcademicYear`; acquires a
    short exclusive row lock on that `AcademicYear` (Owner Decision #36)
    -- held only for this transaction, never across a CP-SAT solve;
    authoritatively rechecks, under that same lock, that no `Schedule`
    has been generated for this year (`docs/DECISIONS.md` #35) --
    raising `school_timetable.application.errors.
    ConfigurationLockedError` if one has, regardless of what an earlier,
    un-locked caller precheck found; reloads the current authoritative
    `SchedulingProblem` under the same lock and invokes the caller-
    supplied `validate` callback against it (never against a stale,
    pre-lock snapshot) -- `validate` is a plain callable over domain
    objects only, so the actual business-rule validation stays owned by
    `application/` (`teaching_assignment_rules`) even though it runs
    inside this port's transaction; and only if `validate` does not
    raise, performs the write and commits, still holding the lock until
    that commit -- never a partial mutation."""

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
        """Inserts one new, plain `TeachingRequirement` row (the domain
        defaults locked by Decision #34: `FLEXIBLE` block policy, empty
        distribution policy, no time preferences, no resource
        requirement, no `split_group_id`) under `natural_id` (already
        generated by the caller -- this port never generates IDs
        itself). Raises `SchedulingProblemNotFoundError` if the
        school/year itself does not resolve."""
        ...

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
        """Full-replacement update of the four narrow editable fields
        on the existing `TeachingRequirement` identified by
        `natural_id`; its own natural ID and every advanced field
        (already proven absent by `validate`) are untouched."""
        ...

    def delete(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        natural_id: str,
        validate: Callable[[SchedulingProblem], None],
    ) -> None:
        """Deletes the existing `TeachingRequirement` identified by
        `natural_id`. The database's own `RESTRICT` foreign keys from
        `schedule_entry`/`locked_occurrence` remain a structural
        backstop, never the primary business rule -- the Decision #35
        schedule-exists recheck above already guarantees no such row
        can exist when this delete is reached through this service."""
        ...


class TeacherRepository(Protocol):
    """Real-School Setup MVP Slice B configuration *write* port --
    narrowly scoped to `Teacher` create/update/delete, mirroring
    `TeachingAssignmentRepository`'s exact lock/recheck/validate
    discipline (shared via `persistence/configuration_write_lock.py`):
    resolves the `AcademicYear` surrogate ID; acquires the short
    exclusive row lock (Owner Decision #36); authoritatively rechecks,
    under that lock, that no `Schedule` has been generated for this
    year (`docs/DECISIONS.md` #35) -- raising `application.errors.
    ConfigurationLockedError` if one has, regardless of what an
    earlier, un-locked caller precheck found; reloads the current
    authoritative `SchedulingProblem` under the same lock and invokes
    the caller-supplied `validate` callback against it (never against a
    stale, pre-lock snapshot); and only if `validate` does not raise,
    performs the write and commits, still holding the lock until that
    commit -- never a partial mutation."""

    def create(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        natural_id: str,
        first_name: str,
        last_name: str,
        validate: Callable[[SchedulingProblem], None],
    ) -> None:
        """Inserts one new `Teacher` row under `natural_id` (already
        generated by the caller -- this port never generates IDs
        itself), with the next ordinal for this `AcademicYear`. Raises
        `SchedulingProblemNotFoundError` if the school/year itself does
        not resolve."""
        ...

    def update(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        natural_id: str,
        first_name: str,
        last_name: str,
        validate: Callable[[SchedulingProblem], None],
    ) -> None:
        """Full-replacement update of `first_name`/`last_name` on the
        existing `Teacher` identified by `natural_id`; its natural ID,
        surrogate ID, and ordinal are untouched."""
        ...

    def delete(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        natural_id: str,
        validate: Callable[[SchedulingProblem], None],
    ) -> None:
        """Deletes the existing `Teacher` identified by `natural_id`.
        `validate` is responsible for rejecting a teacher currently
        referenced by the persisted configuration (`application.errors.
        TeacherInUseError`) before this method is ever reached -- the
        database's own `RESTRICT` foreign keys from
        `teaching_requirement`/`reserved_block` remain a structural
        backstop only, never the primary business rule."""
        ...


class ClassSectionRepository(Protocol):
    """Real-School Setup MVP Slice C configuration *write* port --
    narrowly scoped to the atomic `ClassSection` + canonical
    `WHOLE_CLASS` `ParticipantGroup` + membership aggregate
    (Owner Decision #33), mirroring `TeacherRepository`'s exact
    lock/recheck/validate discipline (shared via
    `persistence/configuration_write_lock.py`): resolves the
    `AcademicYear` surrogate ID; acquires the short exclusive row lock
    (Owner Decision #36); authoritatively rechecks, under that lock,
    that no `Schedule` has been generated for this year
    (`docs/DECISIONS.md` #35) -- raising `application.errors.
    ConfigurationLockedError` if one has, regardless of what an
    earlier, un-locked caller precheck found; reloads the current
    authoritative `SchedulingProblem` under the same lock and invokes
    the caller-supplied `validate` callback against it (never against a
    stale, pre-lock snapshot); and only if `validate` does not raise,
    performs the write and commits, still holding the lock until that
    commit -- never a partial mutation. No generic ParticipantGroup CRUD
    is ever exposed here -- the canonical group is entirely internal to
    this aggregate."""

    def create(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        class_natural_id: str,
        canonical_group_natural_id: str,
        name: str,
        validate: Callable[[SchedulingProblem], None],
    ) -> None:
        """Atomically inserts one new `ClassSection` row, one new
        canonical `WHOLE_CLASS` `ParticipantGroup` row (`name` equal to
        the `ClassSection`'s own name), and the one membership row
        linking them -- under `class_natural_id`/
        `canonical_group_natural_id` (already generated by the caller --
        this port never generates IDs itself). Raises
        `SchedulingProblemNotFoundError` if the school/year itself does
        not resolve."""
        ...

    def update(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        class_natural_id: str,
        name: str,
        validate: Callable[[SchedulingProblem], None],
    ) -> None:
        """Atomically renames the existing `ClassSection` identified by
        `class_natural_id` AND its canonical `WHOLE_CLASS`
        `ParticipantGroup` (resolved by role + exact-one-class
        membership, never by name) to the same new `name`. Every
        natural ID, surrogate ID, ordinal, and membership row is
        untouched."""
        ...

    def delete(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        class_natural_id: str,
        validate: Callable[[SchedulingProblem], None],
    ) -> None:
        """Deletes the existing `ClassSection` identified by
        `class_natural_id` together with its owned canonical
        `WHOLE_CLASS` `ParticipantGroup` and that group's one
        membership row -- atomically, in the safe order the FK graph
        requires (membership, then group, then class). `validate` is
        responsible for rejecting a class currently referenced by the
        persisted configuration (`application.errors.
        ClassSectionInUseError`) before this method is ever reached --
        the database's own `RESTRICT` foreign keys from
        `participant_group_class_section`/`reserved_block_class_section`/
        `teaching_requirement` remain a structural backstop only, never
        the primary business rule. Never deletes a `SUBGROUP`/
        `MERGED_CLASSES` group or any `TeachingRequirement`/
        `ReservedBlock`."""
        ...


class ActivityRepository(Protocol):
    """Real-School Setup MVP Slice D configuration *write* port --
    narrowly scoped to the user-facing "Subject" surface:
    `Activity(kind=ORDINARY)` create/update/delete only, mirroring
    `ClassSectionRepository`'s exact lock/recheck/validate discipline
    (shared via `persistence/configuration_write_lock.py`): resolves
    the `AcademicYear` surrogate ID; acquires the short exclusive row
    lock (Owner Decision #36); authoritatively rechecks, under that
    lock, that no `Schedule` has been generated for this year
    (`docs/DECISIONS.md` #35) -- raising `application.errors.
    ConfigurationLockedError` if one has, regardless of what an earlier,
    un-locked caller precheck found; reloads the current authoritative
    `SchedulingProblem` under the same lock and invokes the caller-
    supplied `validate` callback against it (never against a stale,
    pre-lock snapshot); and only if `validate` does not raise, performs
    the write and commits, still holding the lock until that commit --
    never a partial mutation. `update`/`delete` only ever resolve an
    existing `ORDINARY` activity -- a `CLUB` target is never mutated,
    never distinguished from a missing Subject in any response. `kind`
    is never accepted as input and never mutated after create. Not a
    generic Activity CRUD port -- no Club management is ever exposed
    here."""

    def create(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        subject_natural_id: str,
        name: str,
        validate: Callable[[SchedulingProblem], None],
    ) -> None:
        """Inserts one new `Activity` row with `kind='ORDINARY'` under
        `subject_natural_id` (already generated by the caller -- this
        port never generates IDs itself), with the next ordinal for
        this `AcademicYear` (shared across `ORDINARY` and `CLUB`
        activities -- one ordering domain for the whole table). Raises
        `SchedulingProblemNotFoundError` if the school/year itself does
        not resolve."""
        ...

    def update(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        subject_natural_id: str,
        name: str,
        validate: Callable[[SchedulingProblem], None],
    ) -> None:
        """Full-replacement update of `name` on the existing `Activity`
        identified by `subject_natural_id`; its natural ID, surrogate
        ID, ordinal, and `kind` are untouched. `validate` is
        responsible for confirming the target is `ORDINARY` before this
        method is ever reached."""
        ...

    def delete(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        subject_natural_id: str,
        validate: Callable[[SchedulingProblem], None],
    ) -> None:
        """Deletes the existing `Activity` identified by
        `subject_natural_id`. `validate` is responsible for confirming
        the target is `ORDINARY` and rejecting a subject currently
        referenced by the persisted configuration
        (`application.errors.SubjectInUseError`) before this method is
        ever reached -- the database's own `RESTRICT` foreign keys from
        `teaching_requirement`/`reserved_block` remain a structural
        backstop only, never the primary business rule."""
        ...
