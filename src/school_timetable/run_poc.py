"""Manual PoC entry point: solve the valid synthetic fixture, verify it
independently, and print a readable weekly grid per class.

Run with: python -m school_timetable.run_poc
"""
from __future__ import annotations

from school_timetable.domain.result import EntrySource, SolverStatus
from school_timetable.fixtures.valid_fixture import build_valid_fixture
from school_timetable.scheduling.solver import solve
from school_timetable.verification.verifier import verify


def main() -> None:
    problem = build_valid_fixture()
    result = solve(problem)

    print(f"Solver status: {result.status.value}")
    print(f"Total soft penalty: {result.total_soft_penalty}")
    print(f"Metadata: {result.metadata}")

    if result.status not in (SolverStatus.OPTIMAL, SolverStatus.FEASIBLE):
        print("No schedule produced.")
        return

    report = verify(problem, result.entries)
    print(f"Independent verification passed: {report.passed}")
    for violation in report.violations:
        print(f"  VIOLATION: {violation}")

    periods = sorted(problem.periods, key=lambda p: p.index)
    days = sorted(problem.days, key=lambda d: d.index)

    for class_section in problem.class_sections:
        print(f"\n=== Class {class_section.name} ===")
        header = "period".ljust(10) + "".join(day.name.ljust(20) for day in days)
        print(header)
        for period in periods:
            row = period.name.ljust(10)
            for day in days:
                label = "-"
                for entry in result.entries:
                    if (
                        entry.day_id == day.id
                        and entry.period_id == period.id
                        and class_section.id in entry.class_sections
                    ):
                        prefix = "[club] " if entry.source == EntrySource.RESERVED_BLOCK else ""
                        label = f"{prefix}{entry.activity_id}"
                        break
                row += label.ljust(20)
            print(row)


if __name__ == "__main__":
    main()
