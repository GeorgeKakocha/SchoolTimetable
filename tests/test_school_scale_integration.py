"""Phase 2B scale integration tests: the realistic school-scale scenarios,
actually solved end-to-end.

These invoke CP-SAT (both once to prove the fixture is feasible during
generation, and once more -- completely fresh -- to benchmark the actual
solve) and so are noticeably heavier than the rest of the suite. Marked
``@pytest.mark.slow`` and excluded from the default ``pytest -q`` run
(see pyproject.toml); run them explicitly with ``pytest -q -m slow``.
"""
from __future__ import annotations

import pytest

from school_timetable.domain.result import SolverStatus
from school_timetable.fixtures.school_scale.scenarios import (
    build_school_scale_impossible,
    build_school_scale_standard,
    build_school_scale_tight,
)
from school_timetable.scheduling.options import SolverOptions
from school_timetable.scheduling.solver import solve
from school_timetable.validation.preflight import run_preflight
from school_timetable.verification.verifier import verify

pytestmark = pytest.mark.slow

MAX_TIME_SECONDS = 60.0


def test_school_scale_standard_is_feasible_or_optimal_and_verified():
    assembled = build_school_scale_standard()
    problem = assembled.problem

    assert assembled.stats["num_classes"] == 15
    assert assembled.stats["total_class_slot_units"] == 600
    assert assembled.stats["num_split_groups"] >= 3
    assert assembled.stats["num_merged_requirements"] >= 2

    result = solve(problem, SolverOptions(max_time_seconds=MAX_TIME_SECONDS, random_seed=1))
    assert result.status in (SolverStatus.OPTIMAL, SolverStatus.FEASIBLE), result.metadata

    report = verify(problem, result.entries)
    assert report.passed, report.violations


def test_school_scale_tight_is_feasible_or_optimal_and_verified():
    assembled = build_school_scale_tight()
    problem = assembled.problem

    result = solve(problem, SolverOptions(max_time_seconds=MAX_TIME_SECONDS, random_seed=1))
    assert result.status in (SolverStatus.OPTIMAL, SolverStatus.FEASIBLE), result.metadata

    report = verify(problem, result.entries)
    assert report.passed, report.violations


def test_school_scale_impossible_never_produces_a_valid_schedule():
    problem = build_school_scale_impossible()

    errors = run_preflight(problem)
    if errors:
        # Acceptable outcome: preflight itself proved it can't work.
        return

    result = solve(problem, SolverOptions(max_time_seconds=MAX_TIME_SECONDS, random_seed=1))
    assert result.status == SolverStatus.INFEASIBLE, result.metadata
    assert result.entries == ()


def test_school_scale_standard_is_stable_across_repeated_runs():
    """Not requiring identical placements -- checking the model doesn't
    behave wildly or intermittently fail across repeated solves."""
    assembled = build_school_scale_standard()
    problem = assembled.problem

    for i in range(3):
        result = solve(problem, SolverOptions(max_time_seconds=MAX_TIME_SECONDS, random_seed=i))
        assert result.status in (SolverStatus.OPTIMAL, SolverStatus.FEASIBLE), (i, result.metadata)
        report = verify(problem, result.entries)
        assert report.passed, (i, report.violations)
