"""`GenerateScheduleService` (Phase 3A3.3, `docs/DECISIONS.md` #31): the
first genuine `application/` orchestration service.

Orchestrates, in this exact locked order: existing-schedule precheck ->
load a fully detached `SchedulingProblem` -> explicit preflight ->
solve -> (`OPTIMAL`/`FEASIBLE` only) independent verify -> persist the
first `ScheduleVersion`, atomically. Depends only on the two existing
application-owned repository ports (`application.ports`) plus
`validation.preflight`/`scheduling.solver`/`verification.verifier` --
never SQLAlchemy, persistence concrete adapters, ORM models, FastAPI,
psycopg, or API schemas, even transitively.

No DB `Session`/connection is held by this service itself at any point:
both repository ports open and close their own short session internally
(Owner Decision 4) and this service never touches a `Session` directly
-- the window between the two repository calls (preflight, solve,
verify) is pure, DB-free application code by construction, since
nothing in that window imports anything DB-aware. Owner Decision #36
(Phase 3C.2) closes the resulting configuration-write-vs-generation
race without touching this constraint: `persist_initial_version` itself
re-locks/reloads/compares immediately before persisting, entirely
inside its own short transaction -- this service still never sees a
`Session`.

This module never wires a concrete adapter -- callers (a future Phase
3A3.4 composition root, or a test) construct
`GenerateScheduleService(problem_repository, schedule_repository)`
themselves against whatever concrete `SchedulingProblemRepository`/
`ScheduleVersionRepository` implementations they choose.
"""
from __future__ import annotations

from school_timetable.application.errors import (
    IncompatibleLocksRequireConfirmationError,
    InvalidSchedulingConfigurationError,
    NoActiveScheduleError,
    NoConfigurationDraftError,
    ScheduleAlreadyExistsError,
    ScheduleInfeasibleError,
    StaleScheduleVersionError,
)
from school_timetable.application.ports import (
    ConfigurationRevisionRepository,
    ScheduleVersionRepository,
    SchedulingProblemRepository,
)
from school_timetable.application.schedule_models import ActiveScheduleVersion
from school_timetable.domain.indexing import ProblemIndex
from school_timetable.domain.result import SolverStatus
from school_timetable.domain.schedule import OccurrenceKey
from school_timetable.scheduling.lock_compatibility import classify_locks, resolve_compatible_members
from school_timetable.scheduling.options import SolverOptions
from school_timetable.scheduling.solver import solve
from school_timetable.validation.preflight import run_preflight
from school_timetable.verification.verifier import verify


class ScheduleGenerationError(RuntimeError):
    """Internal defect: the CP-SAT solver itself returned
    `SolverStatus.ERROR` (a model-builder/solver bug, not bad input --
    preflight is expected to have already caught genuinely bad input).
    Never a public application-level outcome, never persisted, never
    mapped to an HTTP 4xx."""


class ScheduleVerificationFailedError(RuntimeError):
    """Internal defect: the solver claimed `OPTIMAL`/`FEASIBLE` but the
    independent verifier (`verification.verifier.verify`) found at
    least one hard-constraint violation in the resulting entries -- a
    solver-modeling bug, never a reason to persist. Never a public
    application-level outcome, never mapped to an HTTP 4xx; the
    underlying plain violation strings are safe to log server-side but
    never surfaced as a valid schedule."""


class GenerateScheduleService:
    """The first genuine `application/` orchestration service (Decision
    #31). Depends only on the two existing repository ports plus
    `validation`/`scheduling`/`verification` -- never SQLAlchemy,
    persistence concrete adapters, ORM models, or FastAPI."""

    def __init__(
        self,
        problem_repository: SchedulingProblemRepository,
        schedule_repository: ScheduleVersionRepository,
        configuration_revision_repository: ConfigurationRevisionRepository | None = None,
    ) -> None:
        self._problem_repository = problem_repository
        self._schedule_repository = schedule_repository
        # Safe Configuration Changes, Slice C, Checkpoint 4: optional,
        # defaulting to `None` so every one of this constructor's many
        # existing two-positional-argument call sites (production
        # wiring in `api/dependencies.py`, every `tests_web/test_*_api.py`
        # fixture, every existing `generate()`-only test) keeps working
        # completely unchanged -- `generate()` itself never touches this
        # dependency. Only `regenerate()` requires it; wiring a concrete
        # adapter through here for real callers is a later checkpoint's
        # minimal dependency-wiring change, deliberately out of scope
        # for this one.
        self._configuration_revision_repository = configuration_revision_repository

    def generate(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        *,
        solver_options: SolverOptions | None = None,
    ) -> ActiveScheduleVersion:
        # (A) Existing-schedule precheck -- avoids an unnecessary
        # expensive solve. Not the final concurrency guarantee: a
        # losing concurrent request still surfaces the same
        # `ScheduleAlreadyExistsError` later, from
        # `persist_initial_version`'s own by-name constraint
        # translation (see (E) below) -- this call's own
        # `SchedulingProblemNotFoundError`/internal-defect exceptions
        # (e.g. a corrupt persisted `Schedule`) are never reinterpreted
        # here, only allowed to propagate.
        existing = self._schedule_repository.get_active_schedule(
            school_natural_id, academic_year_natural_id,
        )
        if existing is not None:
            raise ScheduleAlreadyExistsError(school_natural_id, academic_year_natural_id)

        # (B) Load a complete, fully detached SchedulingProblem. From
        # here through (D)'s verify call, nothing touches a database.
        problem = self._problem_repository.load_by_school_and_year(
            school_natural_id, academic_year_natural_id,
        )

        # (C) Explicit preflight -- kept separate from solve()'s own
        # internal preflight call (never refactored away here) so this
        # service has a clean, explicit InvalidConfiguration boundary.
        preflight_errors = run_preflight(problem)
        if preflight_errors:
            raise InvalidSchedulingConfigurationError(
                school_natural_id, academic_year_natural_id, tuple(preflight_errors),
            )

        # (D) Solve, synchronously, with the actual invocation's options.
        options = solver_options or SolverOptions()
        result = solve(problem, options)

        if result.status == SolverStatus.INFEASIBLE:
            raise ScheduleInfeasibleError(school_natural_id, academic_year_natural_id)

        if result.status == SolverStatus.INVALID_INPUT:
            # Defensive only: the explicit preflight above should
            # already have caught this. Same outcome, never persisted.
            raise InvalidSchedulingConfigurationError(
                school_natural_id, academic_year_natural_id, result.validation_errors,
            )

        if result.status == SolverStatus.ERROR:
            raise ScheduleGenerationError(
                f"solver returned ERROR for school={school_natural_id!r}, "
                f"academic_year={academic_year_natural_id!r}: {result.metadata.get('error')!r}"
            )

        # Only OPTIMAL/FEASIBLE remain -- the independent verifier is a
        # hard gate before any persistence: solver success + verifier
        # failure is always a defect, never persisted, never presented
        # as InvalidConfiguration/Infeasible/ScheduleAlreadyExists.
        report = verify(problem, result.entries)
        if not report.passed:
            raise ScheduleVerificationFailedError(
                f"independent verifier rejected a solver-claimed-successful schedule "
                f"for school={school_natural_id!r}, academic_year={academic_year_natural_id!r}: "
                f"{report.violations!r}"
            )

        # (E) Persist -- only reachable after preflight passed, the
        # solver returned OPTIMAL/FEASIBLE, and the independent verifier
        # passed. `random_seed` is the actual invocation's value,
        # persisted as audit metadata only -- it does not, by itself,
        # guarantee deterministic reproducibility. `problem` (the exact
        # configuration the solver actually solved) is passed through so
        # the repository can perform Owner Decision #36's final
        # lock-protected reload-and-compare immediately before
        # persisting -- if a configuration write committed in the
        # DB-free window between (B) and here, this call raises
        # `ConfigurationChangedDuringGenerationError` instead of
        # persisting a schedule that no longer matches the live
        # configuration.
        return self._schedule_repository.persist_initial_version(
            school_natural_id,
            academic_year_natural_id,
            problem,
            result.entries,
            result.status,
            result.total_soft_penalty,
            result.metadata["wall_time_seconds"],
            options.random_seed,
        )

    def regenerate(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        base_version_number: int,
        confirmed_incompatible_lock_keys: frozenset[OccurrenceKey] = frozenset(),
        *,
        solver_options: SolverOptions | None = None,
    ) -> ActiveScheduleVersion:
        """Safe Configuration Changes, Slice C, Checkpoint 4: regenerates
        the active `Schedule` against the year's open DRAFT
        `ConfigurationRevision` -- a full FRESH solve of the draft using
        the ordinary generation objective (`scheduling.solver.solve`),
        never `scheduling.reoptimize`'s disruption-minimizing one, with
        every still-compatible historical `LockedOccurrence` pinned as a
        HARD constraint. Mirrors `generate()`'s exact shape (cheap
        precheck -> load a fully detached `SchedulingProblem` -> explicit
        preflight -> solve -> independent verify -> persist, atomically)
        plus `ScheduleEditingService`'s stale-base-version/no-active-
        schedule conventions -- no new orchestration pattern invented.

        Every check here is a cheap, early convenience/fast-failure
        short-circuit only: `ScheduleVersionRepository
        .persist_regenerated_version` remains the sole authoritative
        concurrency boundary, re-locking the `AcademicYear` row and
        recomputing every one of these same conditions itself,
        immediately before persisting.
        """
        # (A) An open draft is mandatory -- this is the regeneration-
        # after-configuration-edit path, never a way to re-run a solve
        # against an already-published revision.
        state = self._configuration_revision_repository.get_state(
            school_natural_id, academic_year_natural_id,
        )
        if state.draft_revision_number is None:
            raise NoConfigurationDraftError(school_natural_id, academic_year_natural_id)

        # (B) An existing active Schedule is mandatory (never the way a
        # *first* ScheduleVersion is created), plus the same cheap,
        # early stale-version short-circuit `ScheduleEditingService
        # ._load_active_for_edit` already uses -- no expensive solve for
        # a base version that is already known stale.
        active = self._schedule_repository.get_active_schedule(
            school_natural_id, academic_year_natural_id,
        )
        if active is None:
            raise NoActiveScheduleError(school_natural_id, academic_year_natural_id)
        if active.version_number != base_version_number:
            raise StaleScheduleVersionError(
                school_natural_id, academic_year_natural_id,
                base_version_number, active.version_number,
            )

        # Load the identity and content together, releasing the read transaction
        # before preflight/solve. get_state above is only an early convenience check.
        snapshot = self._problem_repository.load_draft_snapshot(
            school_natural_id, academic_year_natural_id,
        )
        problem = snapshot.problem

        # (D) Same explicit preflight boundary as generate().
        preflight_errors = run_preflight(problem)
        if preflight_errors:
            raise InvalidSchedulingConfigurationError(
                school_natural_id, academic_year_natural_id, tuple(preflight_errors),
            )

        # (E)-(G) Classify the CURRENT active version's own locked
        # occurrences (Checkpoint 1) against the draft problem. A
        # mismatch against the caller's confirmed-incompatible set means
        # either the caller never confirmed at all, or lock compatibility
        # changed since the caller last classified it -- either way, the
        # caller must see the FRESH classification and re-confirm; the
        # solver is never invoked and nothing is persisted. An empty
        # confirmed set is valid, and this proceeds normally, exactly
        # when there is nothing to confirm (current_incompatible_keys is
        # itself empty) -- never a required confirmation for locks that
        # are, in fact, compatible.
        index = ProblemIndex(problem)
        classification = classify_locks(problem, index, active.entries, active.locked_occurrences)
        current_incompatible_keys = frozenset(item.key for item in classification.incompatible)
        if current_incompatible_keys != confirmed_incompatible_lock_keys:
            raise IncompatibleLocksRequireConfirmationError(
                school_natural_id, academic_year_natural_id, classification.incompatible,
            )

        # (H) Resolve every compatible lock's full logical-occurrence
        # membership -- never just its own anchor key -- into the exact
        # CP-SAT lesson-variable keys the fresh solve must pin. Safe:
        # `classify_locks` above already proved every key in
        # `compatible_keys` resolves without error.
        hard_pins = resolve_compatible_members(
            problem, index, active.entries, classification.compatible_keys,
        )

        # (I) A full FRESH solve against the draft -- the ordinary
        # generation model/objective, never reoptimize's disruption-
        # minimizing one -- with every compatible lock pinned as HARD.
        options = solver_options or SolverOptions()
        result = solve(problem, options, hard_pins=hard_pins)

        if result.status == SolverStatus.INFEASIBLE:
            raise ScheduleInfeasibleError(school_natural_id, academic_year_natural_id)

        if result.status == SolverStatus.INVALID_INPUT:
            # Defensive only: the explicit preflight above should
            # already have caught this. Same outcome, never persisted.
            raise InvalidSchedulingConfigurationError(
                school_natural_id, academic_year_natural_id, result.validation_errors,
            )

        if result.status == SolverStatus.ERROR:
            raise ScheduleGenerationError(
                f"solver returned ERROR for school={school_natural_id!r}, "
                f"academic_year={academic_year_natural_id!r}: {result.metadata.get('error')!r}"
            )

        # Only OPTIMAL/FEASIBLE remain -- the same independent-verifier
        # hard gate as generate(), before any persistence.
        report = verify(problem, result.entries)
        if not report.passed:
            raise ScheduleVerificationFailedError(
                f"independent verifier rejected a solver-claimed-successful regeneration "
                f"for school={school_natural_id!r}, academic_year={academic_year_natural_id!r}: "
                f"{report.violations!r}"
            )

        # (J) Persist -- only reachable after preflight passed, lock
        # compatibility was confirmed, the solver returned
        # OPTIMAL/FEASIBLE, and the independent verifier passed.
        # `persist_regenerated_version` remains the authoritative
        # concurrency boundary: it re-locks the AcademicYear row and
        # recomputes every one of (A)-(H) itself immediately before
        # publishing the draft and persisting.
        return self._schedule_repository.persist_regenerated_version(
            school_natural_id,
            academic_year_natural_id,
            base_version_number,
            problem,
            result.entries,
            classification.compatible_keys,
            confirmed_incompatible_lock_keys,
            result.status,
            result.total_soft_penalty,
            result.metadata["wall_time_seconds"],
            options.random_seed,
            expected_draft_revision_id=snapshot.revision_id,
        )
