"""A pure, CP-SAT-free evaluator of the exact soft-objective semantics
`scheduling/model_builder.py`'s CP-SAT model encodes as weighted
objective terms (`_add_preferred_double_constraints`,
`_add_teacher_prefer_not_penalty`, `_add_preferred_period_penalty`,
`_add_min_distinct_days_penalty`).

For a *concrete* solution (a real `tuple[ScheduleEntry, ...]`), every
CP-SAT decision variable's value is already determined by which entries
exist -- there is no optimization ambiguity left to resolve -- so each
term reduces to a deterministic, independent re-derivation from
`problem`/`ScheduleEntry` data alone. This module never imports
`model_builder`/`ortools` and never touches a CP-SAT model; it is a
second, independent implementation of the same weighted sum, not a
refactor of the first.

Exists for exactly one reason (the manual-editing backend persistence
slice, `docs/SCHEDULE_EDITING.md`): a manually-moved candidate schedule
was never itself the output of a fresh CP-SAT solve, so its parent
version's `total_soft_penalty` cannot simply be copied forward the
moment entries actually change -- that would be a lie the instant a
lesson moves relative to a teacher's `PREFER_NOT` slot, a requirement's
preferred periods, its `min_distinct_days` target, or a PREFERRED
double's formation. `evaluate_total_soft_penalty` gives
`application.schedule_editing_service` a truthful number to persist
instead.

Proven, by `tests/test_objective_parity.py`, to agree exactly with
`model_builder`/`scheduling.solver.solve()`'s own CP-SAT-derived
`total_soft_penalty` on real solved schedules -- a disagreement there is
always a modeling bug in one of the two implementations, never expected
drift.
"""
from __future__ import annotations

from collections import defaultdict

from school_timetable.domain.indexing import ProblemIndex
from school_timetable.domain.people import AvailabilityStatus
from school_timetable.domain.problem import SchedulingProblem
from school_timetable.domain.requirements import BlockPolicyMode, TeachingRequirement
from school_timetable.domain.result import EntrySource, ScheduleEntry
from school_timetable.scheduling.weights import (
    MIN_DISTINCT_DAYS_WEIGHT_TIER,
    PREFERRED_DOUBLE_WEIGHT_TIER,
    TEACHER_PREFER_NOT_WEIGHT_TIER,
    weight_value,
)


def evaluate_total_soft_penalty(
    problem: SchedulingProblem,
    entries: tuple[ScheduleEntry, ...],
    index: ProblemIndex | None = None,
) -> int:
    """The exact soft-objective total `model_builder.build_model` would
    compute for a CP-SAT solution that placed exactly these `entries`.

    Never a HARD-feasibility check -- a candidate that is not actually
    HARD-valid can still be scored here; a caller needing a HARD-validity
    gate first uses `verification.verifier.verify` separately, exactly as
    `application.generate_schedule_service`/
    `application.schedule_editing_service` already do before persisting
    anything.
    """
    index = index or ProblemIndex(problem)

    entries_by_requirement: dict[str, list[ScheduleEntry]] = defaultdict(list)
    for e in entries:
        if e.source == EntrySource.REQUIREMENT and e.requirement_id is not None:
            entries_by_requirement[e.requirement_id].append(e)

    return (
        _preferred_double_penalty(index, entries_by_requirement)
        + _teacher_prefer_not_penalty(index, entries_by_requirement)
        + _preferred_period_penalty(index, entries_by_requirement)
        + _min_distinct_days_penalty(index, entries_by_requirement)
    )


def _preferred_double_penalty(
    index: ProblemIndex, entries_by_requirement: dict[str, list[ScheduleEntry]],
) -> int:
    weight = weight_value(PREFERRED_DOUBLE_WEIGHT_TIER)
    penalty = 0
    for req in index.requirements_by_id.values():
        policy = req.block_policy
        if policy.mode != BlockPolicyMode.PREFERRED or not policy.has_double:
            continue
        periods_by_day: dict[str, set[str]] = defaultdict(set)
        for e in entries_by_requirement.get(req.id, ()):
            periods_by_day[e.day_id].add(e.period_id)
        double_formed = any(
            p1.id in day_periods and p2.id in day_periods
            for day_periods in periods_by_day.values()
            for (p1, p2) in index.consecutive_pairs
        )
        if not double_formed:
            penalty += weight
    return penalty


def _teacher_prefer_not_penalty(
    index: ProblemIndex, entries_by_requirement: dict[str, list[ScheduleEntry]],
) -> int:
    weight = weight_value(TEACHER_PREFER_NOT_WEIGHT_TIER)
    penalty = 0
    for req_id, own_entries in entries_by_requirement.items():
        req: TeachingRequirement = index.requirements_by_id[req_id]
        for e in own_entries:
            if index.get_availability(req.teacher_id, e.day_id, e.period_id) == AvailabilityStatus.PREFER_NOT:
                penalty += weight
    return penalty


def _preferred_period_penalty(
    index: ProblemIndex, entries_by_requirement: dict[str, list[ScheduleEntry]],
) -> int:
    penalty = 0
    for req_id, own_entries in entries_by_requirement.items():
        req: TeachingRequirement = index.requirements_by_id[req_id]
        for pref in req.time_preferences:
            weight = weight_value(pref.weight)
            preferred = set(pref.preferred_periods)
            for e in own_entries:
                period = index.periods_by_id[e.period_id]
                if period.index not in preferred:
                    penalty += weight
    return penalty


def _min_distinct_days_penalty(
    index: ProblemIndex, entries_by_requirement: dict[str, list[ScheduleEntry]],
) -> int:
    weight = weight_value(MIN_DISTINCT_DAYS_WEIGHT_TIER)
    penalty = 0
    for req in index.requirements_by_id.values():
        min_days = req.distribution_policy.min_distinct_days
        if min_days is None:
            continue
        distinct_days = len({e.day_id for e in entries_by_requirement.get(req.id, ())})
        deficiency = max(0, min_days - distinct_days)
        penalty += weight * deficiency
    return penalty
