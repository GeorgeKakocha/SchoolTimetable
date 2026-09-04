"""Top-level solver entry point.

Orchestrates preflight validation, CP-SAT model building, solving, and
result conversion. This is the one function most callers need.
"""
from __future__ import annotations

from ortools.sat.python import cp_model

from school_timetable.domain.indexing import ProblemIndex
from school_timetable.domain.problem import SchedulingProblem
from school_timetable.domain.result import SchedulingResult, SolverStatus
from school_timetable.scheduling.model_builder import build_model
from school_timetable.scheduling.options import SolverOptions
from school_timetable.scheduling.result_builder import build_schedule_entries
from school_timetable.validation.preflight import run_preflight

_STATUS_MAP = {
    cp_model.OPTIMAL: SolverStatus.OPTIMAL,
    cp_model.FEASIBLE: SolverStatus.FEASIBLE,
    cp_model.INFEASIBLE: SolverStatus.INFEASIBLE,
}


def solve(problem: SchedulingProblem, options: SolverOptions | None = None) -> SchedulingResult:
    options = options or SolverOptions()

    errors = run_preflight(problem)
    if errors:
        return SchedulingResult(status=SolverStatus.INVALID_INPUT, validation_errors=tuple(errors))

    index = ProblemIndex(problem)
    try:
        built = build_model(problem, index)
    except Exception as exc:  # pragma: no cover - model-builder bug, not bad input
        return SchedulingResult(status=SolverStatus.ERROR, metadata={"error": str(exc)})

    cp_solver = cp_model.CpSolver()
    cp_solver.parameters.max_time_in_seconds = options.max_time_seconds
    cp_solver.parameters.num_search_workers = options.num_search_workers
    if options.random_seed is not None:
        cp_solver.parameters.random_seed = options.random_seed

    raw_status = cp_solver.Solve(built.model)
    mapped_status = _STATUS_MAP.get(raw_status, SolverStatus.ERROR)

    # Benchmark/observability metadata (Phase 2B). These are all cheap,
    # always-available CP-SAT search statistics/model-size counts -- not a
    # general telemetry system, just enough to reason about scaling.
    metadata = {
        "wall_time_seconds": cp_solver.WallTime(),
        "raw_status": cp_solver.StatusName(raw_status),
        "num_conflicts": cp_solver.NumConflicts(),
        "num_branches": cp_solver.NumBranches(),
        "num_lesson_variables": len(built.lesson_vars),
        "num_cp_variables": len(built.model.Proto().variables),
        "num_cp_constraints": len(built.model.Proto().constraints),
    }
    if built.has_objective:
        metadata["best_objective_bound"] = cp_solver.BestObjectiveBound()

    if mapped_status not in (SolverStatus.OPTIMAL, SolverStatus.FEASIBLE):
        return SchedulingResult(status=mapped_status, metadata=metadata)

    entries = build_schedule_entries(problem, built, cp_solver)
    total_penalty = int(cp_solver.ObjectiveValue()) if built.has_objective else 0
    metadata["objective_value"] = total_penalty

    return SchedulingResult(
        status=mapped_status,
        entries=entries,
        total_soft_penalty=total_penalty,
        metadata=metadata,
    )
