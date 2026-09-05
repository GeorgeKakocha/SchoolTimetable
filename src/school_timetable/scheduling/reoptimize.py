"""Re-optimization around a reference schedule (Phase 2C).

Given a ``SchedulingProblem``, a reference ``Schedule`` (with lock state),
and ``SolverOptions``, produce a new valid ``SchedulingResult`` that:

1. Satisfies every original HARD constraint (teacher/group non-overlap,
   full class occupancy, resource capacity, REQUIRED blocks, split sync,
   merged coverage, reserved blocks, fixed placements, max_periods_per_day)
   -- reused unchanged from ``model_builder``, never re-implemented here.
2. Treats every locked logical occurrence as an additional HARD placement.
3. Minimizes disruption from the reference schedule as the TOP soft
   priority.
4. Only among equally-disrupted solutions, optimizes the ordinary
   Phase-1/2A soft preferences (teacher PREFER_NOT, preferred periods,
   preferred double, min_distinct_days).

## Objective priority: two-phase lexicographic solving

Rather than folding disruption into one arbitrary weighted sum with the
existing soft preferences, this uses genuine two-phase lexicographic
optimization -- CP-SAT already supports this cleanly (build the model,
solve once minimizing disruption, pin the proven-optimal disruption value
with an equality constraint, rebuild and solve again minimizing the
ordinary soft objective). No major solver redesign was needed, so no
numeric-weight compromise was made for priority ordering.
"""
from __future__ import annotations

from ortools.sat.python import cp_model

from school_timetable.domain.indexing import ProblemIndex
from school_timetable.domain.problem import SchedulingProblem
from school_timetable.domain.result import SchedulingResult, SolverStatus
from school_timetable.domain.schedule import OccurrenceKey, Schedule
from school_timetable.scheduling.editing import find_logical_occurrence
from school_timetable.scheduling.model_builder import (
    BuiltModel,
    _add_class_occupancy,
    _add_fixed_placements,
    _add_max_periods_per_day,
    _add_min_distinct_days_penalty,
    _add_participant_group_non_overlap,
    _add_preferred_double_constraints,
    _add_preferred_period_penalty,
    _add_required_block_constraints,
    _add_resource_capacity,
    _add_split_group_sync,
    _add_teacher_non_overlap,
    _add_teacher_prefer_not_penalty,
    _add_weekly_fulfillment,
    _create_lesson_variables,
)
from school_timetable.scheduling.options import SolverOptions
from school_timetable.scheduling.result_builder import build_schedule_entries
from school_timetable.validation.errors import ValidationError
from school_timetable.validation.preflight import run_preflight
from school_timetable.verification.verifier import (
    _check_full_class_occupancy,
    _check_merged_group_classes,
    _check_participant_group_non_overlap,
    _check_required_block_patterns,
    _check_split_groups_synchronized,
    _check_teacher_non_overlap,
    _check_weekly_lesson_counts,
)

_STATUS_MAP = {
    cp_model.OPTIMAL: SolverStatus.OPTIMAL,
    cp_model.FEASIBLE: SolverStatus.FEASIBLE,
    cp_model.INFEASIBLE: SolverStatus.INFEASIBLE,
}

OccurrenceGroupKey = tuple  # (requirement_id-or-split_group_id, day_id, anchor_period_id)


def _reference_schedule_structural_violations(
    problem: SchedulingProblem, index: ProblemIndex, entries,
) -> list[str]:
    """Independently check the reference ``Schedule`` has valid
    STRUCTURE/TOPOLOGY -- i.e. that it is a genuine, internally coherent
    timetable safe to decompose into logical occurrences and disruption
    groups -- before it is ever used to build those groups or lock
    constraints.

    This is deliberately **not** "is the reference feasible under the new
    ``problem``". A reference schedule is external, historical input, just
    like ``problem`` is, and both need independent sanity-checking -- but
    they are not sanity-checked for the same thing. The whole point of
    ``reoptimize`` is to repair a reference that a *newly changed*
    HARD condition has just made infeasible (a teacher now UNAVAILABLE
    where it used to teach, a new/tightened resource capacity, a new
    ``ReservedBlock``, a new ``FixedPlacement``, a tightened
    ``max_periods_per_day``, ...); rejecting the reference for exactly one
    of those would make that repair workflow impossible. The new CP-SAT
    model built below (``_build_hard_model``) enforces every one of the
    *current* problem's placement-feasibility rules unconditionally, so
    none of them are weakened by skipping them here -- they simply aren't
    reference-integrity questions.

    What IS checked here is only what "the reference is a genuine
    timetable" requires, independent of whichever placement-feasibility
    rule the new problem may have changed:

    - no duplicate/double-booked teacher or participant-group entries
      (``_check_teacher_non_overlap``, ``_check_participant_group_non_overlap``)
      -- a real timetable can never already contain this;
    - each requirement's entry count matches its (current) weekly count
      (``_check_weekly_lesson_counts``) -- needed to even talk about "this
      requirement's occurrence(s)" consistently;
    - REQUIRED blocks are genuine consecutive same-``block_id`` runs
      (``_check_required_block_patterns``) -- required for
      ``find_logical_occurrence`` to group a block correctly, the exact
      malformation the pre-commit audit found;
    - split-group siblings are synchronized
      (``_check_split_groups_synchronized``) -- required to reconstruct
      one synchronized occurrence across branches;
    - merged-group entries record the right class set
      (``_check_merged_group_classes``) -- required for disruption
      grouping/lock keys to mean what they claim;
    - full class occupancy holds (``_check_full_class_occupancy``) -- a
      reference with gaps or double-bookings in its own right isn't a
      real timetable to begin with.

    Placement-feasibility rules the *current* problem may have legitimately
    changed since the reference was generated -- teacher availability,
    fixed placements, resource capacity, reserved blocks, and
    ``max_periods_per_day`` -- are intentionally NOT checked here. Their
    violation in the reference is expected and is exactly what
    ``reoptimize`` exists to fix; the rebuilt CP-SAT model enforces the
    current versions of all of them regardless.
    """
    violations: list[str] = []
    violations += _check_teacher_non_overlap(entries)
    violations += _check_participant_group_non_overlap(entries)
    violations += _check_weekly_lesson_counts(problem, entries)
    violations += _check_required_block_patterns(problem, index, entries)
    violations += _check_split_groups_synchronized(index, entries)
    violations += _check_merged_group_classes(index, entries)
    violations += _check_full_class_occupancy(problem, index, entries)
    return violations


def _reference_occurrence_groups(
    problem: SchedulingProblem, index: ProblemIndex, schedule: Schedule,
) -> dict[OccurrenceGroupKey, list[tuple[str, str, str]]]:
    """Group reference-schedule entries into disruption-tracking units,
    reusing the exact same logical-occurrence reconstruction the manual
    editing layer uses (``find_logical_occurrence``) -- synchronized split
    siblings collapse into ONE group, a REQUIRED/formed-PREFERRED-double
    block is ONE group, and (critically) FLEXIBLE periods that merely
    happen to land on the same day are correctly kept as SEPARATE groups,
    since the model gives no guarantee they were ever meant to move
    together. Naively grouping by (requirement_id, day_id) alone would
    silently over-glue those.
    """
    groups: dict[OccurrenceGroupKey, list[tuple[str, str, str]]] = {}
    seen: set[tuple[str, str, str]] = set()
    for e in schedule.entries:
        if e.requirement_id is None or (e.requirement_id, e.day_id, e.period_id) in seen:
            continue
        occ = find_logical_occurrence(problem, index, schedule, e.requirement_id, e.day_id, e.period_id)
        anchor = occ.period_ids[0]
        req = index.requirements_by_id[e.requirement_id]
        group_key: OccurrenceGroupKey = (req.split_group_id or e.requirement_id, e.day_id, anchor)
        members = [(m.requirement_id, m.day_id, m.period_id) for m in occ.members]
        groups[group_key] = members
        seen.update(members)
    return groups


def _build_hard_model(problem: SchedulingProblem, index: ProblemIndex):
    model = cp_model.CpModel()
    days = index.days_sorted
    periods = index.instructional_periods_sorted
    requirements = problem.teaching_requirements

    lesson_vars = _create_lesson_variables(model, index, requirements, days, periods)
    _add_teacher_non_overlap(model, index, requirements, lesson_vars, days, periods)
    _add_participant_group_non_overlap(model, requirements, lesson_vars, days, periods)
    _add_class_occupancy(model, index, requirements, lesson_vars, days, periods)
    _add_weekly_fulfillment(model, requirements, lesson_vars, days, periods)
    _add_resource_capacity(model, index, requirements, lesson_vars, days, periods)
    _add_fixed_placements(model, problem, lesson_vars)
    _add_max_periods_per_day(model, requirements, lesson_vars, days, periods)
    _add_split_group_sync(model, index, lesson_vars, days, periods)
    _add_required_block_constraints(model, requirements, lesson_vars, days, periods)
    return model, lesson_vars, days, periods


def _add_lock_constraints(model, schedule: Schedule, lesson_vars, groups: dict) -> None:
    if not schedule.locked_occurrences:
        return
    for (_group_id, day_id, anchor), members in groups.items():
        req_ids = {m[0] for m in members}
        if any(OccurrenceKey(rid, day_id, anchor) in schedule.locked_occurrences for rid in req_ids):
            for m in members:
                model.Add(lesson_vars[m] == 1)


def _add_disruption_terms(model, groups: dict, lesson_vars) -> list:
    disruption_terms = []
    for key, members in groups.items():
        member_vars = [lesson_vars[m] for m in members]
        preserved = model.NewBoolVar(f"preserved_{'_'.join(str(k) for k in key)}")
        for v in member_vars:
            model.Add(preserved <= v)
        model.Add(preserved >= sum(member_vars) - (len(member_vars) - 1))
        disruption_terms.append(1 - preserved)
    return disruption_terms


def reoptimize(
    problem: SchedulingProblem, reference_schedule: Schedule, options: SolverOptions | None = None,
) -> SchedulingResult:
    options = options or SolverOptions()

    errors = run_preflight(problem)
    if errors:
        return SchedulingResult(status=SolverStatus.INVALID_INPUT, validation_errors=tuple(errors))

    # The reference schedule is external input just as much as the problem
    # is: never trust it blindly. Independently re-verify its STRUCTURE/
    # TOPOLOGY -- a genuine, internally coherent timetable safe to
    # decompose into logical occurrences (see
    # ``_reference_schedule_structural_violations`` for exactly what that
    # does and does not cover) -- before building lock constraints or
    # disruption groups from it, both of which would silently misbehave on
    # a malformed schedule otherwise. No unlocked fallback solve is
    # attempted; the reference is never mutated either way. This
    # deliberately does NOT require the reference to satisfy the current
    # problem's placement-feasibility rules (teacher availability, fixed
    # placements, resource capacity, reserved blocks, max_periods_per_day)
    # -- violating exactly one of those is the normal trigger for calling
    # reoptimize in the first place, and the rebuilt model below enforces
    # all of their current-problem versions unconditionally regardless.
    index = ProblemIndex(problem)
    structural_violations = _reference_schedule_structural_violations(
        problem, index, reference_schedule.entries,
    )
    if structural_violations:
        return SchedulingResult(
            status=SolverStatus.INVALID_INPUT,
            validation_errors=(ValidationError(
                "INVALID_REFERENCE_SCHEDULE",
                "The reference schedule is not a structurally valid schedule: "
                + "; ".join(structural_violations),
            ),),
        )

    groups = _reference_occurrence_groups(problem, index, reference_schedule)
    num_groups = len(groups)

    # -- Phase 1: minimize disruption only -----------------------------------
    model1, lesson_vars1, days, periods = _build_hard_model(problem, index)
    _add_lock_constraints(model1, reference_schedule, lesson_vars1, groups)
    disruption_terms1 = _add_disruption_terms(model1, groups, lesson_vars1)
    model1.Minimize(sum(disruption_terms1))

    solver1 = cp_model.CpSolver()
    solver1.parameters.max_time_in_seconds = options.max_time_seconds
    solver1.parameters.num_search_workers = options.num_search_workers
    if options.random_seed is not None:
        solver1.parameters.random_seed = options.random_seed
    status1 = solver1.Solve(model1)
    mapped1 = _STATUS_MAP.get(status1, SolverStatus.ERROR)

    metadata: dict = {
        "phase1_wall_time_seconds": solver1.WallTime(),
        "phase1_raw_status": solver1.StatusName(status1),
        "num_logical_occurrences": num_groups,
    }

    if mapped1 not in (SolverStatus.OPTIMAL, SolverStatus.FEASIBLE):
        return SchedulingResult(status=mapped1, metadata=metadata)

    disruption_value = int(round(solver1.ObjectiveValue()))

    if mapped1 != SolverStatus.OPTIMAL:
        # Time-limited: cannot safely pin a non-proven disruption value and
        # re-solve for soft preferences on top of it. Return phase 1's
        # best-effort valid schedule as-is rather than risk a worse or
        # infeasible phase 2.
        built1 = BuiltModel(model=model1, index=index, lesson_vars=lesson_vars1, has_objective=True)
        entries = build_schedule_entries(problem, built1, solver1)
        metadata.update({
            "wall_time_seconds": solver1.WallTime(),
            "num_moved_occurrences": disruption_value,
            "num_preserved_occurrences": num_groups - disruption_value,
            "disruption_penalty": disruption_value,
            "lexicographic_phase_completed": 1,
        })
        return SchedulingResult(status=mapped1, entries=entries, total_soft_penalty=0, metadata=metadata)

    # -- Phase 2: pin disruption to its proven optimum, minimize ordinary
    #    soft preferences among equally-disrupted solutions. -----------------
    model2, lesson_vars2, days2, periods2 = _build_hard_model(problem, index)
    _add_lock_constraints(model2, reference_schedule, lesson_vars2, groups)
    disruption_terms2 = _add_disruption_terms(model2, groups, lesson_vars2)
    model2.Add(sum(disruption_terms2) == disruption_value)

    requirements = problem.teaching_requirements
    soft_terms: list = []
    soft_terms += _add_preferred_double_constraints(model2, index, requirements, lesson_vars2, days2, periods2)
    soft_terms += _add_teacher_prefer_not_penalty(index, requirements, lesson_vars2, days2, periods2)
    soft_terms += _add_preferred_period_penalty(requirements, lesson_vars2, days2, periods2)
    soft_terms += _add_min_distinct_days_penalty(model2, requirements, lesson_vars2, days2, periods2)
    has_soft_objective = bool(soft_terms)
    if has_soft_objective:
        model2.Minimize(sum(soft_terms))

    solver2 = cp_model.CpSolver()
    solver2.parameters.max_time_in_seconds = options.max_time_seconds
    solver2.parameters.num_search_workers = options.num_search_workers
    if options.random_seed is not None:
        solver2.parameters.random_seed = options.random_seed
    status2 = solver2.Solve(model2)
    mapped2 = _STATUS_MAP.get(status2, SolverStatus.ERROR)

    metadata.update({
        "phase2_wall_time_seconds": solver2.WallTime(),
        "phase2_raw_status": solver2.StatusName(status2),
        "wall_time_seconds": solver1.WallTime() + solver2.WallTime(),
    })

    if mapped2 not in (SolverStatus.OPTIMAL, SolverStatus.FEASIBLE):
        # Should not normally happen: phase 1 already proved a
        # disruption == disruption_value solution exists. Report loudly
        # rather than silently falling back.
        metadata["error"] = (
            f"Phase 2 failed ({solver2.StatusName(status2)}) despite phase 1 proving disruption="
            f"{disruption_value} is achievable; this indicates a phase-2 modeling bug."
        )
        return SchedulingResult(status=SolverStatus.ERROR, metadata=metadata)

    built2 = BuiltModel(model=model2, index=index, lesson_vars=lesson_vars2, has_objective=has_soft_objective)
    entries = build_schedule_entries(problem, built2, solver2)
    soft_penalty = int(solver2.ObjectiveValue()) if has_soft_objective else 0

    metadata.update({
        "num_moved_occurrences": disruption_value,
        "num_preserved_occurrences": num_groups - disruption_value,
        "disruption_penalty": disruption_value,
        "lexicographic_phase_completed": 2,
    })

    return SchedulingResult(status=mapped2, entries=entries, total_soft_penalty=soft_penalty, metadata=metadata)
