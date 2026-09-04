"""Builds a CP-SAT model from a SchedulingProblem.

This is the only module in the codebase allowed to import OR-Tools.
Domain objects go in; a ``BuiltModel`` (CP-SAT model + the lesson decision
variables) comes out. Nothing here decides *whether* the input is sane --
that is preflight's job, done earlier by the caller.
"""
from __future__ import annotations

from dataclasses import dataclass

from ortools.sat.python import cp_model

from school_timetable.domain.calendar import Day, Period
from school_timetable.domain.indexing import ProblemIndex
from school_timetable.domain.people import AvailabilityStatus
from school_timetable.domain.problem import SchedulingProblem
from school_timetable.domain.requirements import BlockPolicyMode, TeachingRequirement
from school_timetable.scheduling.weights import (
    MIN_DISTINCT_DAYS_WEIGHT_TIER,
    PREFERRED_DOUBLE_WEIGHT_TIER,
    TEACHER_PREFER_NOT_WEIGHT_TIER,
    weight_value,
)

LessonKey = tuple[str, str, str]  # (requirement_id, day_id, period_id)


@dataclass
class BuiltModel:
    model: cp_model.CpModel
    index: ProblemIndex
    lesson_vars: dict[LessonKey, cp_model.IntVar]
    has_objective: bool = False


def build_model(problem: SchedulingProblem, index: ProblemIndex | None = None) -> BuiltModel:
    if index is None:
        index = ProblemIndex(problem)

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

    objective_terms: list = []
    objective_terms += _add_block_policy_constraints(model, index, requirements, lesson_vars, days, periods)
    objective_terms += _add_teacher_prefer_not_penalty(index, requirements, lesson_vars, days, periods)
    objective_terms += _add_preferred_period_penalty(requirements, lesson_vars, days, periods)
    objective_terms += _add_min_distinct_days_penalty(model, requirements, lesson_vars, days, periods)

    has_objective = bool(objective_terms)
    if has_objective:
        model.Minimize(sum(objective_terms))

    return BuiltModel(model=model, index=index, lesson_vars=lesson_vars, has_objective=has_objective)


def _create_lesson_variables(
    model: cp_model.CpModel,
    index: ProblemIndex,
    requirements: tuple[TeachingRequirement, ...],
    days: tuple[Day, ...],
    periods: tuple[Period, ...],
) -> dict[LessonKey, cp_model.IntVar]:
    lesson_vars: dict[LessonKey, cp_model.IntVar] = {}
    for req in requirements:
        group = index.participant_groups_by_id[req.participant_group_id]
        for day in days:
            for period in periods:
                var = model.NewBoolVar(f"x_{req.id}_{day.id}_{period.id}")
                lesson_vars[(req.id, day.id, period.id)] = var

                teacher_unavailable = (
                    index.get_availability(req.teacher_id, day.id, period.id)
                    == AvailabilityStatus.UNAVAILABLE
                )
                class_reserved = any(
                    index.is_reserved_for_class(class_id, day.id, period.id)
                    for class_id in group.class_sections
                )
                if teacher_unavailable or class_reserved:
                    model.Add(var == 0)
    return lesson_vars


def _add_teacher_non_overlap(model, index, requirements, lesson_vars, days, periods) -> None:
    reqs_by_teacher: dict[str, list[TeachingRequirement]] = {}
    for req in requirements:
        reqs_by_teacher.setdefault(req.teacher_id, []).append(req)

    for teacher_id, reqs in reqs_by_teacher.items():
        for day in days:
            for period in periods:
                terms = [lesson_vars[(r.id, day.id, period.id)] for r in reqs]
                reserved = 1 if index.is_reserved_for_teacher(teacher_id, day.id, period.id) else 0
                model.Add(sum(terms) + reserved <= 1)


def _add_participant_group_non_overlap(model, requirements, lesson_vars, days, periods) -> None:
    reqs_by_group: dict[str, list[TeachingRequirement]] = {}
    for req in requirements:
        reqs_by_group.setdefault(req.participant_group_id, []).append(req)

    for reqs in reqs_by_group.values():
        for day in days:
            for period in periods:
                terms = [lesson_vars[(r.id, day.id, period.id)] for r in reqs]
                model.Add(sum(terms) <= 1)


def _add_class_occupancy(model, index, requirements, lesson_vars, days, periods) -> None:
    reqs_by_class: dict[str, list[TeachingRequirement]] = {
        class_id: [] for class_id in index.class_sections_by_id
    }
    for req in requirements:
        if not index.is_split_branch_representative(req):
            continue  # the other branch(es) are synchronized, don't double-count occupancy
        group = index.participant_groups_by_id[req.participant_group_id]
        for class_id in group.class_sections:
            if class_id in reqs_by_class:
                reqs_by_class[class_id].append(req)

    for class_id, reqs in reqs_by_class.items():
        for day in days:
            for period in periods:
                if index.is_reserved_for_class(class_id, day.id, period.id):
                    continue  # occupancy already satisfied by the reserved block
                terms = [lesson_vars[(r.id, day.id, period.id)] for r in reqs]
                model.Add(sum(terms) == 1)


def _add_weekly_fulfillment(model, requirements, lesson_vars, days, periods) -> None:
    for req in requirements:
        terms = [lesson_vars[(req.id, d.id, p.id)] for d in days for p in periods]
        model.Add(sum(terms) == req.weekly_periods)


def _add_resource_capacity(model, index, requirements, lesson_vars, days, periods) -> None:
    reqs_by_resource: dict[str, list[TeachingRequirement]] = {}
    for req in requirements:
        if req.resource_requirement is not None:
            reqs_by_resource.setdefault(req.resource_requirement.resource_id, []).append(req)

    for resource_id, reqs in reqs_by_resource.items():
        capacity = index.resources_by_id[resource_id].capacity
        for day in days:
            for period in periods:
                terms = [lesson_vars[(r.id, day.id, period.id)] for r in reqs]
                model.Add(sum(terms) <= capacity)


def _add_fixed_placements(model, problem: SchedulingProblem, lesson_vars) -> None:
    for fp in problem.fixed_placements:
        var = lesson_vars[(fp.requirement_id, fp.slot.day_id, fp.slot.period_id)]
        model.Add(var == 1)


def _add_max_periods_per_day(model, requirements, lesson_vars, days, periods) -> None:
    for req in requirements:
        max_per_day = req.distribution_policy.max_periods_per_day
        if max_per_day is None:
            continue
        for day in days:
            terms = [lesson_vars[(req.id, day.id, p.id)] for p in periods]
            model.Add(sum(terms) <= max_per_day)


def _add_split_group_sync(model, index, lesson_vars, days, periods) -> None:
    for req_ids in index.split_groups.values():
        representative, *others = req_ids
        for other in others:
            for day in days:
                for period in periods:
                    model.Add(
                        lesson_vars[(representative, day.id, period.id)]
                        == lesson_vars[(other, day.id, period.id)]
                    )


def _add_block_policy_constraints(model, index, requirements, lesson_vars, days, periods) -> list:
    """Enforces (or penalizes breaking) at most one double lesson per
    requirement, on a same-block consecutive pair of periods -- see
    ``LessonBlockPolicy`` for the supported-shape limitation."""
    objective_terms: list = []
    pairs = index.consecutive_pairs

    for req in requirements:
        policy = req.block_policy
        if policy.mode == BlockPolicyMode.FLEXIBLE or not policy.has_double:
            continue

        pair_used_vars = []
        for day in days:
            pair_used_today = []
            for (p1, p2) in pairs:
                v1 = lesson_vars[(req.id, day.id, p1.id)]
                v2 = lesson_vars[(req.id, day.id, p2.id)]
                pair_used = model.NewBoolVar(f"pair_{req.id}_{day.id}_{p1.id}_{p2.id}")
                model.Add(pair_used <= v1)
                model.Add(pair_used <= v2)
                model.Add(pair_used >= v1 + v2 - 1)
                pair_used_vars.append(pair_used)
                pair_used_today.append(pair_used)

            # A day holds at most one single lesson for this requirement,
            # unless it is the day hosting the double (then at most two,
            # and -- because pair_used already ties both member periods to
            # 1 -- exactly those two). This is what stops a "single" from
            # being silently stacked onto the double's day, or two
            # non-adjacent periods from masquerading as a double.
            day_total = sum(lesson_vars[(req.id, day.id, p.id)] for p in periods)
            model.Add(day_total <= 1 + sum(pair_used_today))

        if policy.mode == BlockPolicyMode.REQUIRED:
            model.Add(sum(pair_used_vars) == 1)
        elif policy.mode == BlockPolicyMode.PREFERRED:
            model.Add(sum(pair_used_vars) <= 1)
            missing_double = model.NewBoolVar(f"missing_double_{req.id}")
            model.Add(sum(pair_used_vars) + missing_double == 1)
            objective_terms.append(weight_value(PREFERRED_DOUBLE_WEIGHT_TIER) * missing_double)

    return objective_terms


def _add_teacher_prefer_not_penalty(index, requirements, lesson_vars, days, periods) -> list:
    objective_terms: list = []
    weight = weight_value(TEACHER_PREFER_NOT_WEIGHT_TIER)
    reqs_by_teacher: dict[str, list[TeachingRequirement]] = {}
    for req in requirements:
        reqs_by_teacher.setdefault(req.teacher_id, []).append(req)

    for teacher_id, reqs in reqs_by_teacher.items():
        for day in days:
            for period in periods:
                if index.get_availability(teacher_id, day.id, period.id) == AvailabilityStatus.PREFER_NOT:
                    for req in reqs:
                        objective_terms.append(weight * lesson_vars[(req.id, day.id, period.id)])
    return objective_terms


def _add_preferred_period_penalty(requirements, lesson_vars, days, periods) -> list:
    objective_terms: list = []
    for req in requirements:
        for pref in req.time_preferences:
            weight = weight_value(pref.weight)
            preferred = set(pref.preferred_periods)
            for day in days:
                for period in periods:
                    if period.index not in preferred:
                        objective_terms.append(weight * lesson_vars[(req.id, day.id, period.id)])
    return objective_terms


def _add_min_distinct_days_penalty(model, requirements, lesson_vars, days, periods) -> list:
    objective_terms: list = []
    weight = weight_value(MIN_DISTINCT_DAYS_WEIGHT_TIER)

    for req in requirements:
        min_days = req.distribution_policy.min_distinct_days
        if min_days is None:
            continue

        day_used_vars = []
        for day in days:
            day_terms = [lesson_vars[(req.id, day.id, p.id)] for p in periods]
            day_used = model.NewBoolVar(f"day_used_{req.id}_{day.id}")
            model.Add(sum(day_terms) >= 1).OnlyEnforceIf(day_used)
            model.Add(sum(day_terms) == 0).OnlyEnforceIf(day_used.Not())
            day_used_vars.append(day_used)

        distinct_days = sum(day_used_vars)
        deficiency = model.NewIntVar(0, min_days, f"day_deficiency_{req.id}")
        model.Add(deficiency >= min_days - distinct_days)
        objective_terms.append(weight * deficiency)

    return objective_terms
