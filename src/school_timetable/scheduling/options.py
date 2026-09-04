"""Solver-run configuration.

Deliberately a plain, OR-Tools-free dataclass -- it configures *how* CP-SAT
runs (time budget, parallelism, determinism), not the scheduling problem
itself. Kept in ``scheduling/`` rather than ``domain/`` since it is a
solver-execution concern, not part of the school's problem description.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SolverOptions:
    max_time_seconds: float = 30.0
    """Wall-clock budget handed to CP-SAT. A realistic problem may return
    FEASIBLE (not OPTIMAL) if it expires before proving optimality --
    that is an expected, successful outcome, not a failure."""

    num_search_workers: int = 8
    """CP-SAT parallel search worker count. Kept configurable per machine
    rather than hard-coded."""

    random_seed: int | None = None
    """When set, makes CP-SAT's search deterministic across runs (useful
    for reproducible benchmarks). ``None`` leaves OR-Tools' own default
    behavior untouched."""
