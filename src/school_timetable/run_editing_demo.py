"""Phase 2C demonstration: manual move, lock, and re-optimization.

Solves the Phase-1 valid fixture, shows a manual move being validated and
applied, locks the moved lesson plus a few others, introduces one new HARD
condition, re-optimizes, and reports the disruption metric and verifier
result. No UI -- this is a narrated console walkthrough.

Usage: python -m school_timetable.run_editing_demo
"""
from __future__ import annotations

import dataclasses

from school_timetable.domain.people import AvailabilityStatus, TeacherAvailability
from school_timetable.domain.result import SolverStatus
from school_timetable.domain.schedule import Schedule
from school_timetable.fixtures.valid_fixture import build_valid_fixture
from school_timetable.scheduling.editing import apply_move, find_logical_occurrence, lock_occurrence, validate_move
from school_timetable.domain.indexing import ProblemIndex
from school_timetable.scheduling.options import SolverOptions
from school_timetable.scheduling.reoptimize import reoptimize
from school_timetable.scheduling.solver import solve
from school_timetable.verification.verifier import verify


def _print_slot(label, entries, requirement_id):
    slots = sorted((e.day_id, e.period_id) for e in entries if e.requirement_id == requirement_id)
    print(f"{label}: {requirement_id!r} at {slots}")


def main() -> None:
    problem = build_valid_fixture()
    print("=== Step 1: generate the original valid schedule ===")
    solved = solve(problem, SolverOptions(random_seed=11, num_search_workers=1))
    print(f"solver status: {solved.status.value}, soft penalty: {solved.total_soft_penalty}")
    schedule = Schedule(entries=solved.entries)
    index = ProblemIndex(problem)

    # -- Step 2: find two swappable simple occurrences for a manual move --
    print("\n=== Step 2: find a valid manual move ===")
    simple_occs, seen = [], set()
    for e in schedule.entries:
        if e.requirement_id is None or (e.requirement_id, e.day_id, e.period_id) in seen:
            continue
        occ = find_logical_occurrence(problem, index, schedule, e.requirement_id, e.day_id, e.period_id)
        if occ.length == 1 and len(occ.requirement_ids) == 1:
            simple_occs.append((e.requirement_id, e.day_id, e.period_id))
        seen.update((m.requirement_id, m.day_id, m.period_id) for m in occ.members)

    move_source = move_target = None
    for (r1, d1, p1) in simple_occs:
        for (r2, d2, p2) in simple_occs:
            if r1 == r2 or d1 == d2:
                continue
            result = validate_move(problem, schedule, r1, d1, p1, d2, p2, index=index)
            if not result.allowed:
                continue
            # Confirm this is a genuine two-requirement swap and not a
            # same-requirement coincidence (r1 may independently already
            # occupy (d2, p2) via a different occurrence on another day,
            # for the same class, in which case the "occupant" validate_move
            # actually finds there is r1 itself, not r2).
            swapped_req_ids = {e.requirement_id for e in result.plan.removed_entries}
            if swapped_req_ids != {r1, r2}:
                continue
            move_source, move_target, move_result = (r1, d1, p1), (r2, d2, p2), result
            break
        if move_source:
            break

    assert move_source is not None, "expected at least one valid move in this fixture"
    r1, d1, p1 = move_source
    r2, d2, p2 = move_target
    print(f"proposed move: {r1!r} from ({d1!r}, {p1!r}) to ({d2!r}, {p2!r}) -- swaps with {r2!r}")
    print(f"validation allowed: {move_result.allowed}")

    print("\n=== Step 3: apply the validated move ===")
    schedule = apply_move(problem, schedule, move_result)
    _print_slot("after move", schedule.entries, r1)
    _print_slot("after move", schedule.entries, r2)
    report = verify(problem, schedule.entries)
    print(f"independent verification after move: {report.passed}")

    # -- Step 4: lock the moved lesson plus one more occurrence --
    print("\n=== Step 4: lock the moved lesson ===")
    schedule = lock_occurrence(problem, schedule, r1, d2, p2)
    print(f"locked occurrences: {sorted((k.requirement_id, k.day_id) for k in schedule.locked_occurrences)}")

    # -- Step 5: introduce one new HARD condition and re-optimize --
    print("\n=== Step 5: introduce a new constraint and re-optimize ===")
    forced_entry = next(
        e for e in schedule.entries
        if e.teacher_id is not None and e.requirement_id != r1
    )
    changed_problem = dataclasses.replace(
        problem,
        teacher_availabilities=problem.teacher_availabilities + (
            TeacherAvailability(forced_entry.teacher_id, forced_entry.day_id, forced_entry.period_id,
                                 AvailabilityStatus.UNAVAILABLE),
        ),
    )
    print(f"new constraint: teacher {forced_entry.teacher_id!r} now UNAVAILABLE at "
          f"({forced_entry.day_id!r}, {forced_entry.period_id!r})")

    reopt_result = reoptimize(changed_problem, schedule, SolverOptions(max_time_seconds=30, random_seed=1))
    print(f"re-optimization status: {reopt_result.status.value}")

    if reopt_result.status in (SolverStatus.OPTIMAL, SolverStatus.FEASIBLE):
        print(f"num_moved_occurrences: {reopt_result.metadata.get('num_moved_occurrences')}")
        print(f"num_preserved_occurrences: {reopt_result.metadata.get('num_preserved_occurrences')}")
        print(f"total_soft_penalty: {reopt_result.total_soft_penalty}")
        final_report = verify(changed_problem, reopt_result.entries)
        print(f"independent verification after re-optimization: {final_report.passed}")

        locked_slots = {(e.day_id, e.period_id) for e in reopt_result.entries if e.requirement_id == r1}
        held = (d2, p2) in locked_slots
        print(f"locked slot ({d2!r}, {p2!r}) for {r1!r} still present after re-optimization: {held}")


if __name__ == "__main__":
    main()
