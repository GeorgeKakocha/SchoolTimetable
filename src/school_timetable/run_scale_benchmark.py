"""Phase 2B school-scale benchmark runner.

Separate from ``run_poc.py`` (which demonstrates the small Phase-1
fixture end-to-end with a full timetable printout). This runner solves
the realistic school-scale scenarios, repeats the standard/tight
scenarios to check for run-to-run stability, and reports compact
per-run statistics -- no full timetable grid by default.

Usage:
    python -m school_timetable.run_scale_benchmark
    python -m school_timetable.run_scale_benchmark --scenario standard --repeats 3
    python -m school_timetable.run_scale_benchmark --scenario impossible
    python -m school_timetable.run_scale_benchmark --detail standard
"""
from __future__ import annotations

import argparse
import sys

from school_timetable.domain.result import SolverStatus
from school_timetable.fixtures.school_scale.scenarios import (
    build_school_scale_impossible,
    build_school_scale_standard,
    build_school_scale_tight,
)
from school_timetable.scheduling.options import SolverOptions
from school_timetable.scheduling.solver import solve
from school_timetable.verification.verifier import verify

DEFAULT_MAX_TIME_SECONDS = 60.0
DEFAULT_WORKERS = 8


def _print_header() -> None:
    cols = [
        "scenario", "classes", "teachers", "reqs", "slot_units",
        "status", "penalty", "wall_s", "branches", "conflicts", "verifier",
    ]
    print(" | ".join(f"{c:>10}" for c in cols))
    print("-" * (13 * len(cols)))


def _print_row(scenario: str, stats: dict, status, penalty, metadata: dict, verifier_passed) -> None:
    row = [
        scenario,
        stats["num_classes"],
        stats["num_teachers"],
        stats["num_requirements"],
        stats["total_class_slot_units"],
        status.value if hasattr(status, "value") else status,
        penalty,
        f"{metadata.get('wall_time_seconds', float('nan')):.2f}",
        metadata.get("num_branches", "-"),
        metadata.get("num_conflicts", "-"),
        verifier_passed,
    ]
    print(" | ".join(f"{str(c):>10}" for c in row))


def _print_detail(problem, entries) -> None:
    periods = sorted(problem.periods, key=lambda p: p.index)
    days = sorted(problem.days, key=lambda d: d.index)
    for class_section in problem.class_sections:
        print(f"\n=== Class {class_section.name} ===")
        header = "period".ljust(10) + "".join(day.name.ljust(16) for day in days)
        print(header)
        for period in periods:
            row = period.name.ljust(10)
            for day in days:
                label = "-"
                for e in entries:
                    if e.day_id == day.id and e.period_id == period.id and class_section.id in e.class_sections:
                        label = e.activity_id.replace("subject_", "")
                        break
                row += label.ljust(16)
            print(row)


_SCENARIOS = {
    "standard": (build_school_scale_standard, 3),
    "tight": (build_school_scale_tight, 2),
    "impossible": (None, 1),  # built specially: returns a bare SchedulingProblem, no stats wrapper
}


def run_scenario(name: str, repeats: int, options: SolverOptions, detail: bool) -> None:
    if name == "impossible":
        problem = build_school_scale_impossible()
        stats = {
            "num_classes": len(problem.class_sections),
            "num_teachers": len(problem.teachers),
            "num_requirements": len(problem.teaching_requirements),
            "total_class_slot_units": len(problem.class_sections) * len(problem.days)
            * len([p for p in problem.periods if p.is_instructional]),
        }
        for _ in range(repeats):
            result = solve(problem, options)
            verifier_result = "n/a"
            if result.status in (SolverStatus.OPTIMAL, SolverStatus.FEASIBLE):
                verifier_result = verify(problem, result.entries).passed
            _print_row(name, stats, result.status, result.total_soft_penalty, result.metadata, verifier_result)
            if result.status not in (SolverStatus.INFEASIBLE, SolverStatus.INVALID_INPUT):
                print(f"  WARNING: expected INFEASIBLE or INVALID_INPUT for {name!r}, got {result.status}")
            if verifier_result is False:
                print(f"  WARNING: solver reported {result.status.value} for {name!r} but independent verification FAILED")
        return

    builder, _ = _SCENARIOS[name]
    assembled = builder()
    problem = assembled.problem
    for i in range(repeats):
        run_options = SolverOptions(
            max_time_seconds=options.max_time_seconds,
            num_search_workers=options.num_search_workers,
            random_seed=(options.random_seed + i) if options.random_seed is not None else None,
        )
        result = solve(problem, run_options)
        verifier_result = "n/a"
        if result.status in (SolverStatus.OPTIMAL, SolverStatus.FEASIBLE):
            verifier_result = verify(problem, result.entries).passed
        _print_row(name, assembled.stats, result.status, result.total_soft_penalty, result.metadata, verifier_result)
        if result.status not in (SolverStatus.OPTIMAL, SolverStatus.FEASIBLE):
            print(f"  WARNING: expected FEASIBLE or OPTIMAL for {name!r}, got {result.status}")
        if verifier_result is False:
            print(f"  WARNING: solver reported {result.status.value} for {name!r} but independent verification FAILED")
        if detail and i == 0 and result.status in (SolverStatus.OPTIMAL, SolverStatus.FEASIBLE):
            _print_detail(problem, result.entries)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="School-scale solver benchmark (Phase 2B).")
    parser.add_argument(
        "--scenario", choices=["standard", "tight", "impossible", "all"], default="all",
        help="Which scenario(s) to run (default: all).",
    )
    parser.add_argument("--repeats", type=int, default=None, help="Override the default repeat count.")
    parser.add_argument("--max-time-seconds", type=float, default=DEFAULT_MAX_TIME_SECONDS)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--seed", type=int, default=1, help="Base random seed (repeats increment it).")
    parser.add_argument(
        "--detail", action="store_true",
        help="Also print a full weekly timetable grid for the first run of each scenario solved.",
    )
    args = parser.parse_args(argv)

    options = SolverOptions(
        max_time_seconds=args.max_time_seconds, num_search_workers=args.workers, random_seed=args.seed,
    )

    scenarios = ["standard", "tight", "impossible"] if args.scenario == "all" else [args.scenario]

    _print_header()
    for name in scenarios:
        default_repeats = _SCENARIOS[name][1]
        repeats = args.repeats if args.repeats is not None else default_repeats
        run_scenario(name, repeats, options, args.detail)

    return 0


if __name__ == "__main__":
    sys.exit(main())
