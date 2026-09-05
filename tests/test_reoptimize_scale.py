"""Phase 2C scale check: re-optimize the school-scale STANDARD scenario.

Workflow (per the Phase 2C brief): generate/solve the standard scenario,
treat it as the reference schedule, lock a small deterministic subset of
occurrences, introduce one legitimate new HARD condition that requires
some rearrangement, re-optimize with a 60s budget, and verify. We do not
require zero moves -- the problem was deliberately changed. What matters:
locks preserved, result valid, disruption small, runtime practical.
"""
from __future__ import annotations

import dataclasses

import pytest

from school_timetable.domain.people import AvailabilityStatus, TeacherAvailability
from school_timetable.domain.result import SolverStatus
from school_timetable.domain.schedule import Schedule
from school_timetable.fixtures.school_scale.scenarios import build_school_scale_standard
from school_timetable.scheduling.editing import lock_occurrence
from school_timetable.scheduling.options import SolverOptions
from school_timetable.scheduling.reoptimize import reoptimize
from school_timetable.scheduling.solver import solve
from school_timetable.verification.verifier import verify

pytestmark = pytest.mark.slow

MAX_TIME_SECONDS = 60.0


def test_school_scale_standard_reoptimizes_with_locks_and_one_new_constraint():
    assembled = build_school_scale_standard()
    problem = assembled.problem

    reference = solve(problem, SolverOptions(max_time_seconds=MAX_TIME_SECONDS, random_seed=1))
    assert reference.status in (SolverStatus.OPTIMAL, SolverStatus.FEASIBLE)
    schedule = Schedule(entries=reference.entries)

    # 3. Lock a small deterministic subset: the first 5 requirements
    # (sorted by id) and each one's first scheduled day.
    locked_requirement_ids = sorted({e.requirement_id for e in schedule.entries if e.requirement_id})[:5]
    for rid in locked_requirement_ids:
        day_id = sorted(e.day_id for e in schedule.entries if e.requirement_id == rid)[0]
        period_id = next(e.period_id for e in schedule.entries if e.requirement_id == rid and e.day_id == day_id)
        schedule = lock_occurrence(problem, schedule, rid, day_id, period_id)
    assert len(schedule.locked_occurrences) >= 5

    # 4. Introduce one legitimate new HARD condition: pick a teacher/slot
    # combination actually in use by a NON-locked requirement, and make
    # that teacher UNAVAILABLE there -- forcing some rearrangement.
    locked_req_ids_set = {k.requirement_id for k in schedule.locked_occurrences}
    forced_entry = next(
        e for e in schedule.entries
        if e.requirement_id not in locked_req_ids_set and e.teacher_id is not None
    )
    new_availabilities = problem.teacher_availabilities + (
        TeacherAvailability(forced_entry.teacher_id, forced_entry.day_id, forced_entry.period_id,
                             AvailabilityStatus.UNAVAILABLE),
    )
    changed_problem = dataclasses.replace(problem, teacher_availabilities=new_availabilities)

    # 5. Re-optimize.
    result = reoptimize(changed_problem, schedule, SolverOptions(max_time_seconds=MAX_TIME_SECONDS, random_seed=1))

    # 6. Verify.
    assert result.status in (SolverStatus.OPTIMAL, SolverStatus.FEASIBLE), result.metadata
    report = verify(changed_problem, result.entries)
    assert report.passed, report.violations

    # Locks preserved exactly.
    for key in schedule.locked_occurrences:
        matching = [e for e in result.entries if e.requirement_id == key.requirement_id and e.day_id == key.day_id]
        assert matching, f"locked requirement {key.requirement_id!r} missing on {key.day_id!r}"

    # 7. Report.
    print("\n--- Phase 2C scale re-optimization report ---")
    print(f"status: {result.status.value}")
    print(f"wall_time_seconds: {result.metadata.get('wall_time_seconds')}")
    print(f"num_moved_occurrences: {result.metadata.get('num_moved_occurrences')}")
    print(f"num_preserved_occurrences: {result.metadata.get('num_preserved_occurrences')}")
    print(f"total_soft_penalty: {result.total_soft_penalty}")
    print(f"verifier_passed: {report.passed}")

    assert result.metadata["num_moved_occurrences"] >= 1  # the forced change requires at least one move
