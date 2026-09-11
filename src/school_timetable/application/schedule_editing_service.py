"""`ScheduleEditingService` (manual timetable editing backend slice):
the `application/` orchestration layer over the existing, unmodified
domain/scheduling editing core (`scheduling/editing.py`,
`scheduling/reoptimize.py`, `docs/SCHEDULE_EDITING.md`) and the new
`ScheduleVersionRepository.persist_edited_version` port.

Mirrors `GenerateScheduleService`'s exact shape and dependency
discipline: depends only on the two existing repository ports plus
`domain`/`scheduling`/`verification` -- never SQLAlchemy, persistence
concrete adapters, ORM models, or FastAPI, even transitively. No DB
`Session` is held by this service itself at any point.

Every mutating command (`move`/`lock`/`unlock`/`reoptimize`) follows the
same four-step shape:

1. Load the active `ScheduleVersion` and cheaply compare its
   `version_number` against the caller's `base_version_number` --
   `StaleScheduleVersionError` immediately on a mismatch, before any
   expensive work (a re-optimization solve in particular).
2. Invoke the relevant *unmodified* domain function
   (`validate_move`/`apply_move`, `lock_occurrence`/`unlock_occurrence`,
   `reoptimize`) against an in-memory `domain.schedule.Schedule` built
   from the active version's own entries/locked_occurrences -- no
   editing/locking/re-optimization logic is duplicated here.
3. Independently verify the resulting candidate
   (`verification.verifier.verify`) before ever persisting it -- the
   same non-negotiable gate `GenerateScheduleService` already uses.
4. Persist through `ScheduleVersionRepository.persist_edited_version`,
   which re-checks the same base-version condition a second time,
   authoritatively, under the `AcademicYear` row lock -- step 1's check
   is a cheap early rejection, never a replacement for that
   authoritative recheck.

Truthful version metadata (`docs/SCHEDULE_EDITING.md`'s HARD-vs-soft
distinction): a manual move changes entries, so it can never simply
inherit its parent's `solver_status`/`total_soft_penalty` -- this
service persists `SolverStatus.FEASIBLE` (never `OPTIMAL`, which would
falsely claim solver-proven optimality for a hand-edited schedule) plus
a freshly recomputed `total_soft_penalty` via the independent, CP-SAT-
free `scheduling.objective.evaluate_total_soft_penalty` (proven to
agree exactly with the solver's own objective by
`tests/test_objective_parity.py`). A lock/unlock changes no entries at
all, so the active version's own `solver_status`/`total_soft_penalty`
are still exactly true and are carried forward unchanged -- not copied
for convenience, but because nothing they describe has changed. A
re-optimization persists its own actual solver-reported metadata.
"""
from __future__ import annotations

from school_timetable.application.errors import (
    InvalidEditTargetError,
    MoveNotAllowedError,
    NoActiveScheduleError,
    ReoptimizationInfeasibleError,
    ReoptimizationInvalidInputError,
    StaleScheduleVersionError,
)
from school_timetable.application.ports import ScheduleVersionRepository, SchedulingProblemRepository
from school_timetable.application.schedule_editing_models import MovePreviewResult, MovePreviewTarget
from school_timetable.application.schedule_models import ActiveScheduleVersion
from school_timetable.domain.indexing import ProblemIndex
from school_timetable.domain.problem import SchedulingProblem
from school_timetable.domain.result import SolverStatus
from school_timetable.domain.schedule import Schedule
from school_timetable.scheduling.editing import (
    EditingError,
    apply_move,
    find_logical_occurrence,
    lock_occurrence,
    unlock_occurrence,
    validate_move,
)
from school_timetable.scheduling.objective import evaluate_total_soft_penalty
from school_timetable.scheduling.options import SolverOptions
from school_timetable.scheduling.reoptimize import reoptimize as _reoptimize
from school_timetable.verification.verifier import verify


class EditVerificationFailedError(RuntimeError):
    """Internal defect: `apply_move`/`lock_occurrence`/`unlock_occurrence`/
    `reoptimize` produced a candidate the independent verifier rejects --
    a modeling bug in the domain editing/re-optimization core, never a
    reason to persist. Never a public application-level outcome, never
    mapped to an HTTP 4xx."""


class ReoptimizationError(RuntimeError):
    """Internal defect: `reoptimize` itself returned `SolverStatus.ERROR`
    (a model-builder/solver bug, not bad input). Never a public
    application-level outcome, never persisted, never mapped to an HTTP
    4xx."""


class ScheduleEditingService:
    """Depends only on the two existing repository ports plus
    `domain`/`scheduling`/`verification` -- never SQLAlchemy, persistence
    concrete adapters, ORM models, or FastAPI."""

    def __init__(
        self,
        problem_repository: SchedulingProblemRepository,
        schedule_repository: ScheduleVersionRepository,
    ) -> None:
        self._problem_repository = problem_repository
        self._schedule_repository = schedule_repository

    def move(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        base_version_number: int,
        requirement_id: str,
        source_day_id: str,
        source_period_id: str,
        target_day_id: str,
        target_period_id: str,
    ) -> ActiveScheduleVersion:
        problem, schedule, index, _active = self._load_active_for_edit(
            school_natural_id, academic_year_natural_id, base_version_number,
        )

        # `validate_move` never raises for an ordinary bad request (an
        # unknown requirement, an invalid target, a conflict) -- every
        # such case comes back as `MoveValidationResult(allowed=False,
        # violations=...)`, handled uniformly below. It only ever raises
        # `EditingError` for a genuine internal inconsistency (see its
        # own "Internal inconsistency identifying target occupant"
        # comment), which is left to propagate as a real internal
        # defect, never disguised as a client input error.
        result = validate_move(
            problem, schedule, requirement_id, source_day_id, source_period_id,
            target_day_id, target_period_id, index=index,
        )

        if not result.allowed:
            raise MoveNotAllowedError(
                school_natural_id, academic_year_natural_id,
                tuple((v.code, v.message) for v in result.violations),
            )

        try:
            candidate = apply_move(problem, schedule, result, index=index)
        except EditingError as exc:
            # STALE_MOVE_PLAN: the same "not allowed (any more)" outcome
            # as an ordinary validate_move rejection, just discovered at
            # apply time -- surfaced as MoveNotAllowedError, not a raw
            # scheduling exception.
            raise MoveNotAllowedError(
                school_natural_id, academic_year_natural_id,
                ((exc.code or "STALE_MOVE_PLAN", str(exc)),),
            ) from None

        self._verify(school_natural_id, academic_year_natural_id, problem, candidate)
        penalty = evaluate_total_soft_penalty(problem, candidate.entries, index)

        return self._schedule_repository.persist_edited_version(
            school_natural_id, academic_year_natural_id, base_version_number, candidate,
            SolverStatus.FEASIBLE, penalty, wall_time_seconds=0.0, random_seed=None,
        )

    def preview_move(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        base_version_number: int,
        requirement_id: str,
        source_day_id: str,
        source_period_id: str,
    ) -> MovePreviewResult:
        """Read-only: for every OTHER instructional slot, reports exactly
        what `validate_move` would say about moving this source
        occurrence there -- never a second, simplified validity check.
        Persists nothing; creates no `ScheduleVersion`. A caller (the
        `move` command itself) remains the sole authority on whether a
        move actually succeeds -- this only lets a UI show the same
        answer before the user commits to one specific target, so a
        race between preview and the real move is expected and safe
        (the real move re-validates and re-checks the base version
        itself, exactly as it already does without this method existing
        at all)."""
        problem, schedule, index, active = self._load_active_for_edit(
            school_natural_id, academic_year_natural_id, base_version_number,
        )

        # Resolve the source once, up front: an unresolvable source
        # (unknown requirement, or a schedule so malformed
        # find_logical_occurrence itself refuses to reason about it)
        # is reported as the same InvalidEditTargetError every other
        # command uses -- never a preview result where every target is
        # forbidden for the same underlying reason.
        try:
            find_logical_occurrence(problem, index, schedule, requirement_id, source_day_id, source_period_id)
        except EditingError as exc:
            raise InvalidEditTargetError(
                school_natural_id, academic_year_natural_id, exc.code, str(exc),
            ) from None

        targets: list[MovePreviewTarget] = []
        for day in index.days_sorted:
            for period in index.instructional_periods_sorted:
                if day.id == source_day_id and period.id == source_period_id:
                    continue  # never a candidate destination for itself
                result = validate_move(
                    problem, schedule, requirement_id, source_day_id, source_period_id,
                    day.id, period.id, index=index,
                )
                targets.append(MovePreviewTarget(
                    day_id=day.id,
                    period_id=period.id,
                    allowed=result.allowed,
                    violations=tuple((v.code, v.message) for v in result.violations),
                ))

        return MovePreviewResult(version_number=active.version_number, targets=tuple(targets))

    def lock(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        base_version_number: int,
        requirement_id: str,
        day_id: str,
        period_id: str,
    ) -> ActiveScheduleVersion:
        problem, schedule, index, active = self._load_active_for_edit(
            school_natural_id, academic_year_natural_id, base_version_number,
        )
        try:
            candidate = lock_occurrence(problem, schedule, requirement_id, day_id, period_id, index=index)
        except EditingError as exc:
            raise InvalidEditTargetError(
                school_natural_id, academic_year_natural_id, exc.code, str(exc),
            ) from None

        self._verify(school_natural_id, academic_year_natural_id, problem, candidate)

        # Entries are unchanged -- the active version's own solver_status/
        # total_soft_penalty remain exactly true, not copied for
        # convenience.
        return self._schedule_repository.persist_edited_version(
            school_natural_id, academic_year_natural_id, base_version_number, candidate,
            active.solver_status, active.total_soft_penalty, wall_time_seconds=0.0, random_seed=None,
        )

    def unlock(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        base_version_number: int,
        requirement_id: str,
        day_id: str,
        period_id: str,
    ) -> ActiveScheduleVersion:
        problem, schedule, index, active = self._load_active_for_edit(
            school_natural_id, academic_year_natural_id, base_version_number,
        )
        try:
            candidate = unlock_occurrence(problem, schedule, requirement_id, day_id, period_id, index=index)
        except EditingError as exc:
            raise InvalidEditTargetError(
                school_natural_id, academic_year_natural_id, exc.code, str(exc),
            ) from None

        self._verify(school_natural_id, academic_year_natural_id, problem, candidate)

        return self._schedule_repository.persist_edited_version(
            school_natural_id, academic_year_natural_id, base_version_number, candidate,
            active.solver_status, active.total_soft_penalty, wall_time_seconds=0.0, random_seed=None,
        )

    def reoptimize(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        base_version_number: int,
        *,
        solver_options: SolverOptions | None = None,
    ) -> ActiveScheduleVersion:
        problem, schedule, _index, _active = self._load_active_for_edit(
            school_natural_id, academic_year_natural_id, base_version_number,
        )
        options = solver_options or SolverOptions()

        result = _reoptimize(problem, schedule, options)

        if result.status == SolverStatus.INFEASIBLE:
            raise ReoptimizationInfeasibleError(school_natural_id, academic_year_natural_id)
        if result.status == SolverStatus.INVALID_INPUT:
            raise ReoptimizationInvalidInputError(
                school_natural_id, academic_year_natural_id, result.validation_errors,
            )
        if result.status == SolverStatus.ERROR:
            raise ReoptimizationError(
                f"reoptimize returned ERROR for school={school_natural_id!r}, "
                f"academic_year={academic_year_natural_id!r}: {result.metadata.get('error')!r}"
            )

        report = verify(problem, result.entries)
        if not report.passed:
            raise EditVerificationFailedError(
                f"independent verifier rejected a reoptimize-claimed-successful schedule "
                f"for school={school_natural_id!r}, academic_year={academic_year_natural_id!r}: "
                f"{report.violations!r}"
            )

        # Locks are a re-optimization input (HARD constraints the solve
        # respects), never an output it changes -- the candidate carries
        # forward exactly the same locked_occurrences that were fed in.
        candidate = schedule.with_entries(result.entries)

        return self._schedule_repository.persist_edited_version(
            school_natural_id, academic_year_natural_id, base_version_number, candidate,
            result.status, result.total_soft_penalty,
            wall_time_seconds=result.metadata["wall_time_seconds"], random_seed=options.random_seed,
        )

    def _load_active_for_edit(
        self, school_natural_id: str, academic_year_natural_id: str, base_version_number: int,
    ) -> tuple[SchedulingProblem, Schedule, ProblemIndex, ActiveScheduleVersion]:
        active = self._schedule_repository.get_active_schedule(school_natural_id, academic_year_natural_id)
        if active is None:
            raise NoActiveScheduleError(school_natural_id, academic_year_natural_id)
        if active.version_number != base_version_number:
            # Cheap, early rejection using data already loaded -- never a
            # substitute for persist_edited_version's own authoritative
            # recheck under the AcademicYear row lock immediately before
            # commit (see step 4 in the module docstring).
            raise StaleScheduleVersionError(
                school_natural_id, academic_year_natural_id,
                base_version_number, active.version_number,
            )

        problem = self._problem_repository.load_by_school_and_year(
            school_natural_id, academic_year_natural_id,
        )
        schedule = Schedule(entries=active.entries, locked_occurrences=active.locked_occurrences)
        index = ProblemIndex(problem)
        return problem, schedule, index, active

    def _verify(
        self,
        school_natural_id: str,
        academic_year_natural_id: str,
        problem: SchedulingProblem,
        candidate: Schedule,
    ) -> None:
        report = verify(problem, candidate.entries)
        if not report.passed:
            raise EditVerificationFailedError(
                f"independent verifier rejected an edit-produced schedule for "
                f"school={school_natural_id!r}, academic_year={academic_year_natural_id!r}: "
                f"{report.violations!r}"
            )
