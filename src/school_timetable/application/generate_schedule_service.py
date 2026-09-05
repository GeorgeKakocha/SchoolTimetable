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
nothing in that window imports anything DB-aware.

This module never wires a concrete adapter -- callers (a future Phase
3A3.4 composition root, or a test) construct
`GenerateScheduleService(problem_repository, schedule_repository)`
themselves against whatever concrete `SchedulingProblemRepository`/
`ScheduleVersionRepository` implementations they choose.
"""
from __future__ import annotations

from school_timetable.application.errors import (
    InvalidSchedulingConfigurationError,
    ScheduleAlreadyExistsError,
    ScheduleInfeasibleError,
)
from school_timetable.application.ports import ScheduleVersionRepository, SchedulingProblemRepository
from school_timetable.application.schedule_models import ActiveScheduleVersion
from school_timetable.domain.result import SolverStatus
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
    ) -> None:
        self._problem_repository = problem_repository
        self._schedule_repository = schedule_repository

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
        # guarantee deterministic reproducibility.
        return self._schedule_repository.persist_initial_version(
            school_natural_id,
            academic_year_natural_id,
            result.entries,
            result.status,
            result.total_soft_penalty,
            result.metadata["wall_time_seconds"],
            options.random_seed,
        )
