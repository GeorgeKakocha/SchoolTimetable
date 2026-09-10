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
`TeacherAvailabilityRepository` (Owner Decision #38) is the fifth,
narrowly scoped to one mutation -- atomically replacing one Teacher's
entire sparse `PREFER_NOT`/`UNAVAILABLE` exception set -- never
per-cell create/update/delete, and never a generic constraint
repository. `SpecialActivityRepository` (Reserved Activities Slice
A1) is the sixth, a deliberate sibling to `ActivityRepository` rather
than an extension of it -- narrowly scoped to the user-facing "Special
Activity" write surface, `Activity(kind=CLUB)` create/update/delete
only, never `ORDINARY` activities and never a `kind` mutation;
`ActivityRepository`'s own docstring already commits to never exposing
Club management, so that guarantee is preserved by keeping this a
separate port rather than widening it. `update` additionally
synchronizes every persisted `ReservedBlock.name` referencing the
renamed Activity, in the same transaction as the Activity rename
itself (`ReservedBlock.name` is server-derived from its Activity's
`name`, never independent user input). `ReservedActivityRepository`
(Reserved Activities Slice A2) is the seventh, narrowly scoped to the
user-facing "Reserved Activity" write surface -- `ReservedBlock`
create/update/delete as one atomic whole-aggregate replacement (its
own `class_sections`/`slots` children are always replaced wholesale,
never diffed). Unlike every prior write port, `create`/`update` return
the written aggregate's own canonically-ordered fields directly
(`application.reserved_activity_models.ReservedActivityWriteResult`)
rather than `None` -- the persisted child order depends on
canonicalization (authoritative `ClassSection` order;
`Day.index`/`Period.index` order) only the repository performs, so
returning it avoids duplicating that ordering logic in the caller.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from school_timetable.application.reserved_activity_models import ReservedActivityFields, ReservedActivityWriteResult
from school_timetable.application.schedule_models import ActiveScheduleVersion
from school_timetable.application.teacher_availability_models import TeacherAvailabilityExceptionFields
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
        resource_id: str | None = None,
    ) -> None:
        """Inserts one new, plain `TeachingRequirement` row (the domain
        defaults locked by Decision #34: `FLEXIBLE` block policy, empty
        distribution policy, no time preferences, no `split_group_id`)
        under `natural_id` (already generated by the caller -- this
        port never generates IDs itself). `resource_id=None` (the
        default, for backward compatibility with callers predating
        Resources B1) means no fixed Resource; a non-`None` value has
        already been proven to resolve in this school/year by
        `validate`. Raises `SchedulingProblemNotFoundError` if the
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
        resource_id: str | None = None,
    ) -> None:
        """Full-replacement update of the narrow editable fields on the
        existing `TeachingRequirement` identified by `natural_id`; its
        own natural ID and every remaining advanced field (already
        proven absent by `validate`) are untouched. `resource_id`
        follows the same full-replacement contract as every other
        field here (Resources B1) -- `None` (the default) always clears
        any currently assigned Resource; there is no partial-update
        "leave unchanged" option."""
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


class TeacherAvailabilityRepository(Protocol):
    """Teacher Availability configuration *write* port (Owner Decision
    #38) -- narrowly scoped to one mutation: atomically reconciling one
    Teacher's entire sparse exception set, mirroring
    `TeacherRepository`'s exact lock/recheck/validate discipline
    (shared via `persistence/configuration_write_lock.py`): resolves
    the `AcademicYear` surrogate ID; acquires the short exclusive row
    lock (Owner Decision #36); authoritatively rechecks, under that
    lock, that no `Schedule` has been generated for this year
    (`docs/DECISIONS.md` #35) -- raising `application.errors.
    ConfigurationLockedError` if one has, regardless of what an
    earlier, un-locked caller precheck found; reloads the current
    authoritative `SchedulingProblem` under the same lock and invokes
    the caller-supplied `validate` callback against it (never against
    a stale, pre-lock snapshot); and only if `validate` does not
    raise, reconciles the persisted rows and commits, still holding
    the lock until that commit -- never a partial mutation. This is
    NOT per-cell create/update/delete -- the complete desired
    exception set for one Teacher is the unit of mutation, and
    `AVAILABLE` is never a row this port ever writes."""

    def replace_exceptions(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        teacher_natural_id: str,
        exceptions: tuple[TeacherAvailabilityExceptionFields, ...],
        validate: Callable[[SchedulingProblem], None],
    ) -> None:
        """Reconciles the persisted `TeacherAvailability` rows for
        `teacher_natural_id` to match `exceptions` exactly: existing
        cells no longer desired are deleted, an existing cell whose
        status changed is updated in place (preserving its `ordinal`),
        and newly-desired cells are inserted with the next `ordinal`
        (assigned across the whole `AcademicYear`, sorted by
        `Day.index`/`Period.index` for deterministic insertion order --
        never by request order). Raises `SchedulingProblemNotFoundError`
        if the school/year itself does not resolve."""
        ...


class SpecialActivityRepository(Protocol):
    """Reserved Activities Slice A1 configuration *write* port --
    narrowly scoped to the user-facing "Special Activity" surface:
    `Activity(kind=CLUB)` create/update/delete only, mirroring
    `ActivityRepository`'s exact lock/recheck/validate discipline
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
    existing `CLUB` activity -- an `ORDINARY` target is never mutated,
    never distinguished from a missing Special Activity in any
    response. `kind` is never accepted as input and never mutated
    after create. Not a generic Activity CRUD port -- no Subject
    management is ever exposed here."""

    def create(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        special_activity_natural_id: str,
        name: str,
        validate: Callable[[SchedulingProblem], None],
    ) -> None:
        """Inserts one new `Activity` row with `kind='CLUB'` under
        `special_activity_natural_id` (already generated by the caller
        -- this port never generates IDs itself), with the next ordinal
        for this `AcademicYear` (shared across `ORDINARY` and `CLUB`
        activities -- one ordering domain for the whole table). Raises
        `SchedulingProblemNotFoundError` if the school/year itself does
        not resolve."""
        ...

    def update(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        special_activity_natural_id: str,
        name: str,
        validate: Callable[[SchedulingProblem], None],
    ) -> None:
        """Full-replacement update of `name` on the existing `Activity`
        identified by `special_activity_natural_id`; its natural ID,
        surrogate ID, ordinal, and `kind` are untouched. `validate` is
        responsible for confirming the target is `CLUB` before this
        method is ever reached. In the SAME transaction, every
        persisted `ReservedBlock` row whose `activity_id` references
        this Activity has its own `name` column set to the same
        normalized new `name` -- `ReservedBlock.name` is a server-
        derived mirror of its Activity's `name`, never independent
        input, so it can never go stale relative to a rename. Blocks
        referencing a different Activity are never touched."""
        ...

    def delete(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        special_activity_natural_id: str,
        validate: Callable[[SchedulingProblem], None],
    ) -> None:
        """Deletes the existing `Activity` identified by
        `special_activity_natural_id`. `validate` is responsible for
        confirming the target is `CLUB` and rejecting a special
        activity currently referenced by a persisted `ReservedBlock`
        (`application.errors.SpecialActivityInUseError`) before this
        method is ever reached -- the database's own `RESTRICT` foreign
        key from `reserved_block` remains a structural backstop only,
        never the primary business rule."""
        ...


class ReservedActivityRepository(Protocol):
    """Reserved Activities Slice A2 configuration *write* port --
    narrowly scoped to the user-facing "Reserved Activity" surface:
    `ReservedBlock` create/update/delete only, mirroring every other
    configuration writer's exact lock/recheck/validate discipline
    (shared via `persistence/configuration_write_lock.py`): resolves
    the `AcademicYear` surrogate ID; acquires the short exclusive row
    lock (Owner Decision #36); authoritatively rechecks, under that
    lock, that no `Schedule` has been generated for this year
    (`docs/DECISIONS.md` #35) -- raising `application.errors.
    ConfigurationLockedError` if one has; reloads the current
    authoritative `SchedulingProblem` under the same lock and invokes
    the caller-supplied `validate` callback against it; and only if
    `validate` does not raise, performs the write and commits, still
    holding the lock until that commit -- never a partial mutation.
    `create`/`update` always replace the block's `class_sections`/
    `slots` children wholesale (delete-and-reinsert, canonically
    ordered) -- there is no independent child identity to diff
    against. `ReservedBlock.name` is always recomputed from the
    referenced Special Activity's current `name`, never accepted as
    input."""

    def create(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        reserved_activity_natural_id: str,
        fields: ReservedActivityFields,
        validate: Callable[[SchedulingProblem], None],
    ) -> ReservedActivityWriteResult:
        """Inserts one new `ReservedBlock` row under
        `reserved_activity_natural_id` (already generated by the
        caller -- this port never generates IDs itself), with the next
        ordinal for this `AcademicYear`, plus canonically-ordered
        `ReservedBlockClassSection`/`ReservedBlockSlot` child rows.
        Raises `SchedulingProblemNotFoundError` if the school/year
        itself does not resolve."""
        ...

    def update(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        reserved_activity_natural_id: str,
        fields: ReservedActivityFields,
        validate: Callable[[SchedulingProblem], None],
    ) -> ReservedActivityWriteResult:
        """Whole-aggregate replacement of the existing `ReservedBlock`
        identified by `reserved_activity_natural_id`: its
        `activity_id`/`teacher_id`/derived `name` are updated in
        place, and its `class_sections`/`slots` children are deleted
        and reinserted in canonical order. Its natural ID and ordinal
        are untouched. `validate` is responsible for confirming the
        target exists before this method is ever reached."""
        ...

    def delete(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        reserved_activity_natural_id: str,
        validate: Callable[[SchedulingProblem], None],
    ) -> None:
        """Deletes the existing `ReservedBlock` identified by
        `reserved_activity_natural_id`, cascading its own
        `class_sections`/`slots` children only -- never the referenced
        Special Activity, Teacher, ClassSection, Day, or Period.
        `validate` is responsible for confirming the target exists
        before this method is ever reached."""
        ...


class ResourceRepository(Protocol):
    """Resources Slice A configuration *write* port -- the Resource
    catalog (`domain.resources.Resource(id, name, capacity)`)
    create/update/delete surface, mirroring
    `SpecialActivityRepository`'s exact lock/recheck/validate
    discipline (shared via `persistence/configuration_write_lock.py`):
    resolves the `AcademicYear` surrogate ID; acquires the short
    exclusive row lock (Owner Decision #36); authoritatively rechecks,
    under that lock, that no `Schedule` has been generated for this
    year (`docs/DECISIONS.md` #35) -- raising `application.errors.
    ConfigurationLockedError` if one has, regardless of what an earlier,
    un-locked caller precheck found; reloads the current authoritative
    `SchedulingProblem` under the same lock and invokes the caller-
    supplied `validate` callback against it (never against a stale,
    pre-lock snapshot); and only if `validate` does not raise, performs
    the write and commits, still holding the lock until that commit --
    never a partial mutation."""

    def create(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        resource_natural_id: str,
        name: str,
        capacity: int,
        validate: Callable[[SchedulingProblem], None],
    ) -> None:
        """Inserts one new `Resource` row under `resource_natural_id`
        (already generated by the caller -- this port never generates
        IDs itself), with the next ordinal in the Resource catalog's
        own, separate ordinal sequence (never shared with Activity/
        Teacher/ClassSection/any other catalog). Raises
        `SchedulingProblemNotFoundError` if the school/year itself does
        not resolve."""
        ...

    def update(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        resource_natural_id: str,
        name: str,
        capacity: int,
        validate: Callable[[SchedulingProblem], None],
    ) -> None:
        """Full-replacement update of `name`/`capacity` on the existing
        `Resource` identified by `resource_natural_id`; its natural ID,
        surrogate ID, and ordinal are untouched. `validate` is
        responsible for confirming the target exists before this
        method is ever reached."""
        ...

    def delete(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        resource_natural_id: str,
        validate: Callable[[SchedulingProblem], None],
    ) -> None:
        """Deletes the existing `Resource` identified by
        `resource_natural_id`. `validate` is responsible for
        confirming the target exists and rejecting a resource currently
        referenced by a persisted `TeachingRequirement.resource_id`
        (`application.errors.ResourceInUseError`) before this method is
        ever reached -- the database's own `RESTRICT` foreign key from
        `teaching_requirement` remains a structural backstop only,
        never the primary defense."""
        ...
