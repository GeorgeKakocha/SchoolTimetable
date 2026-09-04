"""CP-SAT-based construction of a known-feasible canonical witness schedule.

This is fixture-generation infrastructure only, run once while *building*
a school-scale fixture -- never part of the benchmark under test.

Why CP-SAT here at all, rather than a hand-rolled constructor: a
zero-slack (every class exactly fully occupied), ~230-requirement,
~35-teacher scheduling problem is exactly the kind of densely-packed
combinatorial problem where a single-pass greedy heuristic can dead-end on
an unlucky early commitment even though a valid global arrangement
clearly exists (this was tried first and hit exactly that wall -- see
docs/SCALE_VALIDATION.md for the concrete failure and the reasoning behind
this design choice). CP-SAT performing real search is simply the more
reliable tool for constructing ONE witness, and it is already the
fully-validated engine from Phases 1-2A.

This does not make the later benchmark circular or leak information: the
witness solve here is a *separate* solver invocation, used only to (a)
prove the declared curriculum is genuinely feasible before it is shipped
as a fixture, and (b) source a small, explicitly chosen set of
``FixedPlacement`` values (exactly as a school would pin a few lessons).
Everything else about the witness is discarded. The benchmark that
actually measures Phase 2B's solver performance calls ``scheduling.solve``
completely fresh, with its own ``SolverOptions``, against the final
problem (curriculum + those chosen fixed placements) -- no hidden
placement information beyond the deliberately configured fixed lessons
ever crosses from this module into that run.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ortools.sat.python import cp_model

from school_timetable.domain.indexing import ProblemIndex
from school_timetable.domain.problem import SchedulingProblem
from school_timetable.scheduling.model_builder import build_model
from school_timetable.scheduling.result_builder import build_schedule_entries
from school_timetable.validation.preflight import run_preflight


class FixtureGenerationError(RuntimeError):
    """Raised when a declared curriculum cannot be proven feasible -- a
    curriculum/capacity design problem, never silently ignored or worked
    around by weakening a constraint."""


@dataclass
class CanonicalSchedule:
    """The witness schedule: ``placements[requirement_id]`` is the list of
    (day_id, period_id) pairs CP-SAT chose for that requirement in this one
    witness solve. Used only to source a handful of genuine
    ``FixedPlacement`` values -- never fed to the solver wholesale, and
    never reused across the later independent benchmark solve.
    """

    placements: dict[str, list[tuple[str, str]]] = field(default_factory=dict)


def build_canonical_schedule(
    problem: SchedulingProblem,
    *,
    max_time_seconds: float = 120.0,
    random_seed: int = 20260904,
) -> CanonicalSchedule:
    """Prove ``problem`` (with no fixed placements yet) is feasible by
    solving it once, and return the resulting placements as a witness.
    """
    errors = run_preflight(problem)
    if errors:
        raise FixtureGenerationError(
            "Curriculum fails preflight before any witness could be constructed: "
            + "; ".join(f"{e.code}: {e.message}" for e in errors)
        )

    index = ProblemIndex(problem)
    built = build_model(problem, index)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = max_time_seconds
    solver.parameters.num_search_workers = 8
    solver.parameters.random_seed = random_seed
    status = solver.Solve(built.model)

    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        raise FixtureGenerationError(
            f"Could not construct a known-feasible witness schedule: CP-SAT returned "
            f"{solver.StatusName(status)} within {max_time_seconds}s. This means the "
            "declared curriculum is not actually feasible as designed -- a "
            "curriculum/capacity design problem, not a benchmark result."
        )

    entries = build_schedule_entries(problem, built, solver)
    placements: dict[str, list[tuple[str, str]]] = {}
    for e in entries:
        if e.requirement_id is not None:
            placements.setdefault(e.requirement_id, []).append((e.day_id, e.period_id))

    return CanonicalSchedule(placements=placements)
