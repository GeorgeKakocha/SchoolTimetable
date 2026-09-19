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
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from school_timetable.application.synchronized_split_models import (
        CreateSynchronizedSplitCommand,
        SynchronizedSplitPublicIds,
    )

from school_timetable.application.calendar_models import DayWriteResult, PeriodFields, PeriodWriteResult
from school_timetable.application.configuration_revision_models import ConfigurationRevisionState
from school_timetable.application.reserved_activity_models import ReservedActivityFields, ReservedActivityWriteResult
from school_timetable.application.schedule_models import (
    ActiveScheduleVersion,
    ScheduleVersionSnapshot,
    ScheduleVersionSummary,
)
from school_timetable.application.school_provisioning_models import (
    ProvisionSchoolWithInitialYearCommand,
    ProvisionSchoolWithInitialYearResult,
)
from school_timetable.application.teacher_availability_models import TeacherAvailabilityExceptionFields
from school_timetable.application.draft_snapshot import DraftConfigurationSnapshot
from school_timetable.domain.problem import SchedulingProblem
from school_timetable.domain.result import ScheduleEntry, SolverStatus
from school_timetable.domain.schedule import OccurrenceKey, Schedule


class SchoolProvisioningRepository(Protocol):
    """Atomic persistence boundary for a School and its initial year."""

    def provision_school_with_initial_year(
        self, command: ProvisionSchoolWithInitialYearCommand,
    ) -> ProvisionSchoolWithInitialYearResult:
        """Create School + AcademicYear + initial DRAFT revision, or
        return an exactly equivalent existing aggregate on replay.

        Raises explicit application conflicts for incompatible existing
        identities.  No partial aggregate may be committed.
        """
        ...


class SchedulingProblemRepository(Protocol):
    def load_draft_snapshot(
        self, school_natural_id: str, academic_year_natural_id: str,
    ) -> DraftConfigurationSnapshot:
        """Load the current draft identity and its complete problem consistently.

        Raises NoConfigurationDraftError when no draft exists. The factory-backed
        adapter releases its transaction before returning; never hold it over solve.
        The revision ID is an internal concurrency token, never an HTTP field.
        """
        ...

    def load_by_school_and_year(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
    ) -> SchedulingProblem:
        """Load the complete persisted scheduling configuration for one
        school's one academic year, reconstructed as a frozen
        `SchedulingProblem` with exact tuple order and no persistence
        surrogate ID anywhere in the result.

        Safe Configuration Changes, Slice A/B: resolves the year's
        currently *editable* `ConfigurationRevision` -- its open DRAFT
        if one exists, else its PUBLISHED revision (the pre-first-
        Generate case, where only a draft exists, is the special case
        of this same rule). Deliberately draft-first: once Slice B's
        "Begin editing configuration" reopens a draft alongside an
        existing published revision, every Setup/config-read caller and
        every configuration writer's own `validate` closure must see
        the configuration actually being edited, never the
        now-frozen published one. Never mixes rows from two different
        revisions.

        Raises `school_timetable.application.errors.
        SchedulingProblemNotFoundError` identically whether
        `school_natural_id` itself is unknown or it is known but
        `academic_year_natural_id` is not -- both are the same
        "this configuration does not exist" outcome to the caller.
        """
        ...

    def load_for_revision(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        revision_number: int,
    ) -> SchedulingProblem:
        """Load the complete persisted scheduling configuration for one
        SPECIFIC `ConfigurationRevision`, identified by its natural
        `revision_number` (never a persistence surrogate ID) -- for
        historical `ScheduleVersion` projection, which must resolve
        against the exact revision its entries were generated/edited
        against, never "whatever is currently published/draft".

        Raises `SchedulingProblemNotFoundError` if the school/year
        itself does not resolve, matching `load_by_school_and_year`
        exactly. `revision_number` is always sourced from an existing
        `ScheduleVersion`'s own `configuration_revision_number` by
        every caller of this method -- a revision a `ScheduleVersion`
        references is never deleted, so an unresolvable
        `revision_number` here would be an internal defect, not an
        ordinary client-facing outcome, and is never disguised as one.
        """
        ...


class ConfigurationRevisionRepository(Protocol):
    """Safe Configuration Changes, Slice B: the draft configuration
    lifecycle port -- read the current revision state, open a new
    editable draft (eagerly cloned from the current published
    revision), and discard an open draft. Deliberately narrow: no
    generic `ConfigurationRevision` CRUD, no regeneration (Slice C),
    and never exposes a persistence surrogate ID.

    `api/configuration_revision_routes.py` depends on this Protocol
    directly, the same way `api/config_routes.py`'s existing `GET
    /config` route depends on `SchedulingProblemRepository` directly --
    none of the three methods here has any additional application-level
    orchestration beyond what the concrete adapter already does inside
    its own `AcademicYear`-row-locked transaction, so no separate
    `application/` service class wraps this port."""

    def get_state(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
    ) -> ConfigurationRevisionState:
        """Read-only. Raises `SchedulingProblemNotFoundError` if the
        school/year itself does not resolve, matching every other
        repository port's convention exactly."""
        ...

    def begin_draft(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
    ) -> ConfigurationRevisionState:
        """Opens (or returns) this year's editable draft, under the
        same `AcademicYear` row lock (Owner Decision #36) every
        configuration writer already uses -- concurrent callers can
        never produce two drafts.

        - No published revision exists yet (pre-first-Generate): the
          year's initial draft already exists; returned unchanged, no
          new revision is created.
        - A draft already exists (this method was already called, or
          Generate has not run since): idempotent -- returned
          unchanged, no new revision is created, no re-clone happens.
        - A published revision exists and no draft is open: a NEW
          `ConfigurationRevision` (the next `revision_number` for this
          year, `status=DRAFT`) is created, and every one of the
          fifteen `SchedulingProblem` configuration tables is eagerly,
          atomically cloned from the published revision into it --
          same natural IDs, same field values, every intra-
          configuration relationship remapped to the NEW draft rows
          (never a raw copy of the published revision's own surrogate
          FK values). `published_revision_id` is untouched; no
          `ScheduleVersion`/`ScheduleEntry`/`LockedOccurrence` row is
          ever read or written. A clone failure rolls back the entire
          new revision -- the draft pointer is never set to a
          partially-cloned revision.
        """
        ...

    def discard_draft(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
    ) -> ConfigurationRevisionState:
        """Permanently deletes this year's open draft and everything in
        it, under the same `AcademicYear` row lock, and returns the
        resulting state (`draft_revision_number=None`,
        `configuration_locked=True`). The published revision (if any)
        and every `Schedule`/`ScheduleVersion`/`ScheduleEntry`/
        `LockedOccurrence` row are completely untouched.

        Raises `NoConfigurationDraftError` if no draft is currently
        open. Raises `InitialDraftCannotBeDiscardedError` if this
        year's only revision is its initial pre-first-Generate draft
        (no published revision exists) -- that draft is the year's only
        editable configuration and is required for its first Generate
        to ever succeed. Defense-in-depth: if any `ScheduleVersion`
        somehow references this draft (Slice A's own invariant says
        this is unreachable -- a revision is never published in place;
        it is atomically replaced by a *different*, freshly-published
        revision), the discard is refused and zero writes are performed
        rather than relying solely on the RESTRICT/CASCADE FK behavior
        to fail loudly -- raised as a bare `RuntimeError` (never a named
        application error), matching this codebase's convention for
        internal-invariant guards that are unreachable in practice.
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
        """Persists a manually-edited or re-optimized in-memory
        `Schedule` (the domain type from `domain.schedule` -- entries
        plus `locked_occurrences`) as a new, immutable `ScheduleVersion`,
        and atomically promotes it to active. The manual-editing core
        (`scheduling/editing.py`/`scheduling/reoptimize.py`) is entirely
        in-memory and produces this `candidate` value; this method is
        the one and only way it becomes durable.

        `base_version_number` is the `version_number` the caller loaded
        and edited/re-optimized *from* -- never a persistence surrogate
        ID, matching `ActiveScheduleVersion` itself never exposing one.
        Under the same Owner-Decision-#36 `AcademicYear` row lock
        `persist_initial_version` already uses, this method reloads the
        actual current active version and compares its `version_number`
        against `base_version_number`; if they differ (someone else's
        edit/re-optimization was promoted first), it raises
        `school_timetable.application.errors.StaleScheduleVersionError`
        and persists nothing at all -- no `ScheduleVersion`,
        `ScheduleEntry`, or `LockedOccurrence` row, and
        `active_version_id` is left untouched.

        On success, exactly one new `ScheduleVersion` is created with
        `parent_version_id` set to the previous active version's
        surrogate ID, `version_number` one past the highest existing
        version number for this `Schedule`, its own full set of
        `ScheduleEntry` rows (from `candidate.entries`) and its own
        `LockedOccurrence` rows (from `candidate.locked_occurrences`) --
        never touching any prior version's rows -- and
        `Schedule.active_version_id` is atomically repointed to it. No
        migration, rollback, version-activation, or history-listing
        capability is implied by this method; it only ever creates the
        next version linearly from the currently active one.

        Raises `SchedulingProblemNotFoundError` if the school/year
        itself does not resolve, and `CorruptScheduleStateError`
        (persistence-internal; see `schedule_repository.py`) if no
        `Schedule`/active version exists yet to edit at all -- this
        method is never the way a *first* `ScheduleVersion` is created
        (`persist_initial_version` is).
        """
        ...

    def persist_regenerated_version(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        base_version_number: int,
        problem: SchedulingProblem,
        entries: tuple[ScheduleEntry, ...],
        locked_occurrences: frozenset[OccurrenceKey],
        confirmed_incompatible_lock_keys: frozenset[OccurrenceKey],
        solver_status: SolverStatus,
        total_soft_penalty: int,
        wall_time_seconds: float,
        random_seed: int | None,
        *,
        expected_draft_revision_id: int,
    ) -> ActiveScheduleVersion:
        """Safe Configuration Changes, Slice C: publishes the year's open
        DRAFT `ConfigurationRevision` and creates the next `ScheduleVersion`
        under the year's single existing `Schedule`, atomically -- the
        third and last way a `ScheduleVersion.configuration_revision_id`
        is ever set (alongside `persist_initial_version`'s first-publish
        and `persist_edited_version`'s always-copied-forward-unchanged
        cases). Requires an existing `Schedule` (unlike
        `persist_initial_version`) and an open draft (unlike
        `persist_edited_version`, which never changes revision) -- never
        the way a *first* `ScheduleVersion` is created.

        `expected_draft_revision_id` is the non-reused database row identity
        returned together with `problem` by load_draft_snapshot. Under the year
        lock, reject a different current draft ID with
        ConfigurationChangedDuringGenerationError before any publication.
        Revision numbers are not identity tokens: discard/reopen may reuse them.

        `problem` is the exact draft `SchedulingProblem` the solver
        actually solved; `entries` are its resulting `ScheduleEntry`
        values. `locked_occurrences` are exactly the `OccurrenceKey`s
        `scheduling.lock_compatibility.classify_locks` found COMPATIBLE
        and the caller pinned as HARD constraints in that same solve --
        these, and only these, become the new version's own
        `LockedOccurrence` rows; every confirmed-incompatible key is
        dropped, never persisted. `confirmed_incompatible_lock_keys` is
        the exact natural-ID set of incompatible locks the caller has
        been shown (via a prior `IncompatibleLocksRequireConfirmationError`
        or an equivalent preview) and explicitly agreed to drop -- empty
        when the active version had no locks, or when every lock was
        found compatible.

        Authoritative persistence sequence, all inside one transaction
        under a single `AcademicYear` row lock (`SELECT ... FOR UPDATE`,
        Owner Decision #36 -- the same primitive `persist_initial_version`/
        `persist_edited_version` already use), any failure at any step
        rolling back the entire transaction with zero rows written:

        1. Acquire the `AcademicYear` row lock.
        2. Reload the year's CURRENT draft `SchedulingProblem` under that
           lock.
        3. Compare it to `problem` (the exact configuration actually
           solved).
        4. If they differ -- a configuration write committed in the
           DB-free window between the caller's load/solve and this call --
           raise `school_timetable.application.errors.
           ConfigurationChangedDuringGenerationError` (reused, never a
           Slice-C-specific duplicate); zero rows written.
        5. Reload the CURRENT active `ScheduleVersion` and compare its
           `version_number` to `base_version_number`.
        6. If they differ -- another edit/re-optimization/regeneration was
           promoted to active first -- raise `school_timetable.
           application.errors.StaleScheduleVersionError` (reused); zero
           rows written; `Schedule.active_version_id` left untouched.
        7. Recompute lock compatibility (`scheduling.lock_compatibility.
           classify_locks`) against the reloaded, authoritative draft
           `problem` and the reloaded active version's own historical
           entries/`locked_occurrences` -- never trusting the caller's
           own, possibly-stale, `problem`/classification from step 3-4's
           comparison alone.
        8. Compare that fresh classification's incompatible-key set to
           `confirmed_incompatible_lock_keys`.
        9. If they differ -- compatibility changed in the race window, or
           the caller never actually confirmed the current set -- raise
           `school_timetable.application.errors.
           IncompatibleLocksRequireConfirmationError` carrying the FRESH
           classification; zero rows written.
        10. Publish the draft: `ConfigurationRevision.status = "PUBLISHED"`,
            `AcademicYear.published_revision_id` set to it,
            `AcademicYear.draft_revision_id` cleared -- the exact
            publish transition `persist_initial_version` already performs,
            generalized to the *N*-th revision; every prior PUBLISHED
            revision, and every `ScheduleVersion` referencing one,
            remains completely untouched (the multi-published-revision
            invariant this depends on is Slice A's own, already-fixed
            guarantee).
        11. Insert exactly one new `ScheduleVersion`
            (`version_number` = one past the highest existing
            `version_number` for this `Schedule` -- never just
            `base_version_number + 1` -- `parent_version_id` set to the
            previous active version's surrogate ID).
        12. Its `configuration_revision_id` is the just-published
            revision (never copied forward unchanged, unlike
            `persist_edited_version` -- this is the one case where the
            revision genuinely changes).
        13. Persist its own `ScheduleEntry` rows from `entries` -- never
            touching any prior version's rows.
        14. Persist its own `LockedOccurrence` rows from `locked_occurrences`
            ONLY -- every confirmed-incompatible key is simply never
            written.
        15. Atomically repoint `Schedule.active_version_id` to the new
            version.
        16. Commit.
        17. Any exception at any point (including within steps 10-15)
            rolls back the ENTIRE transaction -- no partially-published
            revision, no orphan `ScheduleVersion`/`ScheduleEntry`/
            `LockedOccurrence` row ever survives; the previous active
            version remains active and every historical `ScheduleVersion`/
            `ConfigurationRevision` remains immutable and independently
            resolvable exactly as before the call.

        Raises `SchedulingProblemNotFoundError` if the school/year itself
        does not resolve, `ConfigurationChangedDuringGenerationError` if the
        expected draft is no longer current (including no draft), and
        `CorruptScheduleStateError` (persistence-internal)
        if no `Schedule`/active version exists yet -- this method is never
        the way a *first* `ScheduleVersion` is created.

        No schema/migration change is required for any of this -- every
        table and constraint already exists (proven structurally by
        `tests_web/test_persistence_schema.py::
        test_future_regeneration_publish_transition_is_schema_valid`).
        """
        ...

    def list_versions(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
    ) -> tuple[ScheduleVersionSummary, ...] | None:
        """Schedule version history (schedule version history + restore
        slice): every `ScheduleVersion` for this school/year's
        `Schedule`, newest `version_number` first, metadata only -- never
        `entries`/`locked_occurrences` (call `get_version` for a
        specific version's full state). Exactly one item has
        `is_active=True`.

        Returns `None` if no `Schedule` has been generated yet for this
        school/year -- the same "ordinary, expected outcome, not an
        error" convention `get_active_schedule` already uses. Raises
        `SchedulingProblemNotFoundError` if the school/year itself does
        not resolve.
        """
        ...

    def get_version(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        version_number: int,
    ) -> ScheduleVersionSnapshot | None:
        """One specific `ScheduleVersion`'s full state -- its own
        `entries`/`locked_occurrences`, exactly as originally persisted,
        regardless of whether it happens to be the currently active one
        (`ScheduleVersionSnapshot.is_active` says which). Never mutates
        anything; a read-only sibling to `get_active_schedule`, used by
        historical class/teacher timetable projection and as a restore
        command's copy source.

        Returns `None` only if no `Schedule` exists at all yet for this
        school/year (mirrors `get_active_schedule`'s own convention).
        Raises `school_timetable.application.errors.
        ScheduleVersionNotFoundError` if a `Schedule` exists but no
        `ScheduleVersion` with this `version_number` does -- never
        silently falls back to the active version. Raises
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


class SynchronizedSplitRepository(Protocol):
    """One atomic write of exactly two synchronized SUBGROUP branches.

    The adapter follows the same AcademicYear row-lock, current-draft
    resolution, locked-configuration rejection, and reload-under-lock
    validation discipline as every other configuration writer.
    """

    def create(
        self,
        command: "CreateSynchronizedSplitCommand",
        public_ids: "SynchronizedSplitPublicIds",
        validate: Callable[[SchedulingProblem], None],
    ) -> None:
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


class CalendarDayRepository(Protocol):
    """Calendar A configuration *write* port -- the Day catalog
    (`domain.calendar.Day(id, name, index)`) create/update/delete/move
    surface, mirroring `ResourceRepository`'s exact lock/recheck/
    validate discipline (Owner Decision #36).

    `create`/`update` return the written Day's own resolved fields
    (including its authoritative `index`, which `create` alone assigns
    -- always "append to end") rather than `None`, so the caller never
    has to duplicate that assignment. `delete` reindexes every
    remaining Day to stay contiguous `0..N-1` in the same transaction.
    `move` swaps `day_natural_id` with its immediate `direction`
    neighbor, atomically, and returns `None` (its own `name` never
    changes; only `index` does, for the two swapped Days)."""

    def create(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        day_natural_id: str,
        name: str,
        validate: Callable[[SchedulingProblem], None],
    ) -> DayWriteResult:
        """Inserts one new Day row under `day_natural_id` (already
        generated by the caller), appended at the end of the existing
        Day sequence (`index = len(existing Days)`). Raises
        `SchedulingProblemNotFoundError` if the school/year itself does
        not resolve."""
        ...

    def update(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        day_natural_id: str,
        name: str,
        validate: Callable[[SchedulingProblem], None],
    ) -> DayWriteResult:
        """Renames the existing Day identified by `day_natural_id`;
        its natural ID, surrogate ID, and `index` are untouched.
        `validate` is responsible for confirming the target exists
        before this method is ever reached."""
        ...

    def delete(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        day_natural_id: str,
        validate: Callable[[SchedulingProblem], None],
    ) -> None:
        """Deletes the existing Day identified by `day_natural_id`,
        then reindexes every remaining Day (in their existing relative
        order) to stay contiguous `0..N-1`, in the same transaction.
        `validate` is responsible for confirming the target exists,
        that at least one other Day remains, and rejecting a Day
        currently referenced by a persisted `TeacherAvailability`/
        `ReservedBlock` slot/`FixedPlacement` (`DayInUseError`) before
        this method is ever reached -- the database's own `RESTRICT`
        foreign keys remain a structural backstop only."""
        ...

    def move(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        day_natural_id: str,
        direction: str,
        validate: Callable[[SchedulingProblem], None],
    ) -> None:
        """Swaps `index` between `day_natural_id` and its immediate
        neighbor in `direction` (`"up"`/`"down"`), atomically. `validate`
        is responsible for confirming the target exists and is not
        already at the corresponding edge."""
        ...


class CalendarPeriodRepository(Protocol):
    """Calendar A configuration *write* port -- the Period catalog
    (`domain.calendar.Period`) create/update/delete/move surface,
    mirroring `CalendarDayRepository`'s exact discipline, extended
    with deterministic `block_id` recomputation (never a public field --
    callers submit `starts_new_block: bool` instead, see
    `domain.calendar.recompute_block_ids`) and the "IMPORTANT
    is_instructional POLICY" preservation rule: every write here leaves
    each row's own `is_instructional` exactly as it already was (new
    rows are always inserted `True`; an existing row's flag, legacy
    `False` included, is never silently flipped).

    `create`/`update` return the written Period's own resolved fields
    (including its authoritative `index` and derived `starts_new_block`)
    rather than `None`, avoiding duplicating the block-recomputation
    logic in the caller -- the exact same reasoning
    `ReservedActivityRepository` already documents for its own
    `create`/`update`."""

    def create(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        period_natural_id: str,
        fields: PeriodFields,
        validate: Callable[[SchedulingProblem], None],
    ) -> PeriodWriteResult:
        """Inserts one new, always-`is_instructional=True` Period row
        under `period_natural_id`, appended at the end of the existing
        Period sequence (`index = len(existing Periods)`), then
        recomputes `block_id` for the complete new sequence from
        `fields.starts_new_block` plus every other Period's own current
        marker (`domain.calendar.derive_starts_new_block`). Raises
        `SchedulingProblemNotFoundError` if the school/year itself does
        not resolve."""
        ...

    def update(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        period_natural_id: str,
        fields: PeriodFields,
        validate: Callable[[SchedulingProblem], None],
    ) -> PeriodWriteResult:
        """Full-replacement update of `name`/`start_time`/`end_time` on
        the existing Period identified by `period_natural_id`, plus a
        full `block_id` recomputation reflecting `fields.starts_new_block`
        for this Period (every other Period keeps its own current
        marker). `is_instructional`, natural ID, surrogate ID, and
        `index` are untouched. `validate` is responsible for confirming
        the target exists before this method is ever reached."""
        ...

    def delete(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        period_natural_id: str,
        validate: Callable[[SchedulingProblem], None],
    ) -> None:
        """Deletes the existing Period identified by `period_natural_id`,
        then reindexes every remaining Period (in their existing
        relative order) to stay contiguous `0..N-1` and recomputes
        `block_id` for the complete remaining sequence, in the same
        transaction. `validate` is responsible for confirming the
        target exists, the minimum-calendar and TimePreference
        index-drift-safety invariants (`PeriodInUseError`), before this
        method is ever reached."""
        ...

    def move(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        period_natural_id: str,
        direction: str,
        validate: Callable[[SchedulingProblem], None],
    ) -> None:
        """Swaps `index` between `period_natural_id` and its immediate
        neighbor in `direction` (`"up"`/`"down"`), atomically, then
        recomputes `block_id` for the complete new sequence -- each
        Period's own `starts_new_block` marker travels with its row
        identity across the swap (see `domain.calendar.
        recompute_block_ids`'s docstring). `validate` is responsible
        for confirming the target exists, is not already at the
        corresponding edge, and that no `TimePreference` exists
        anywhere in the Academic Year (`PeriodReorderBlockedError`)."""
        ...
