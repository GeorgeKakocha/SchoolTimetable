"""End-to-end acceptance test: the valid synthetic fixture must solve and
every phase-1 hard constraint must hold, confirmed independently of
CP-SAT by the verifier.
"""
from __future__ import annotations

from collections import defaultdict

import pytest

from school_timetable.domain.people import AvailabilityStatus
from school_timetable.domain.result import EntrySource, SolverStatus
from school_timetable.fixtures.valid_fixture import build_valid_fixture
from school_timetable.scheduling.solver import solve
from school_timetable.verification.verifier import verify


@pytest.fixture(scope="module")
def solved():
    problem = build_valid_fixture()
    result = solve(problem)
    return problem, result


def test_valid_fixture_is_feasible_or_optimal(solved):
    _, result = solved
    assert result.status in (SolverStatus.OPTIMAL, SolverStatus.FEASIBLE)


def test_independent_verifier_passes(solved):
    problem, result = solved
    report = verify(problem, result.entries)
    assert report.passed, report.violations


def test_every_weekly_period_is_scheduled(solved):
    problem, result = solved
    counts = defaultdict(int)
    for e in result.entries:
        if e.requirement_id:
            counts[e.requirement_id] += 1
    for req in problem.teaching_requirements:
        assert counts[req.id] == req.weekly_periods


def test_no_teacher_overlaps(solved):
    _, result = solved
    seen = set()
    for e in result.entries:
        if e.teacher_id is None:
            continue
        key = (e.teacher_id, e.day_id, e.period_id)
        assert key not in seen, f"teacher double-booked: {key}"
        seen.add(key)


def test_no_participant_group_overlaps(solved):
    _, result = solved
    seen = set()
    for e in result.entries:
        if e.participant_group_id is None:
            continue
        key = (e.participant_group_id, e.day_id, e.period_id)
        assert key not in seen, f"participant group double-booked: {key}"
        seen.add(key)


def test_unavailable_teacher_slots_never_used(solved):
    problem, result = solved
    unavailable = {
        (a.teacher_id, a.day_id, a.period_id)
        for a in problem.teacher_availabilities
        if a.status == AvailabilityStatus.UNAVAILABLE
    }
    for e in result.entries:
        if e.teacher_id is not None:
            assert (e.teacher_id, e.day_id, e.period_id) not in unavailable


def test_club_blocks_respected(solved):
    problem, result = solved
    for block in problem.reserved_blocks:
        for slot in block.slots:
            for class_id in block.class_sections:
                clashing = [
                    e for e in result.entries
                    if e.requirement_id is not None
                    and class_id in e.class_sections
                    and e.day_id == slot.day_id
                    and e.period_id == slot.period_id
                ]
                assert clashing == []


def test_required_double_lesson_is_consecutive_and_same_block(solved):
    problem, result = solved
    periods_by_id = {p.id: p for p in problem.periods}
    entries = [e for e in result.entries if e.requirement_id == "math_8a"]
    by_day = defaultdict(list)
    for e in entries:
        by_day[e.day_id].append(e.period_id)

    double_days = [d for d, ps in by_day.items() if len(ps) == 2]
    assert len(double_days) == 1

    p1, p2 = sorted(by_day[double_days[0]], key=lambda pid: periods_by_id[pid].index)
    pa, pb = periods_by_id[p1], periods_by_id[p2]
    assert pb.index == pa.index + 1
    assert pa.block_id == pb.block_id


def test_required_double_lesson_does_not_cross_lunch_boundary(solved):
    _, result = solved
    entries = [e for e in result.entries if e.requirement_id == "math_8a"]
    by_day = defaultdict(list)
    for e in entries:
        by_day[e.day_id].append(e.period_id)
    double_days = [d for d, ps in by_day.items() if len(ps) == 2]
    periods_used = set(by_day[double_days[0]])
    assert periods_used != {"p4", "p5"}


def test_german_russian_split_branches_are_synchronized(solved):
    _, result = solved
    german_slots = {
        (e.day_id, e.period_id) for e in result.entries if e.requirement_id == "german_8a"
    }
    russian_slots = {
        (e.day_id, e.period_id) for e in result.entries if e.requirement_id == "russian_8a"
    }
    assert german_slots == russian_slots
    assert len(german_slots) == 3


def test_merged_lesson_occupies_both_source_classes(solved):
    _, result = solved
    merged = [e for e in result.entries if e.requirement_id == "history_merged_9a_9b"]
    assert len(merged) == 1
    assert set(merged[0].class_sections) == {"9a", "9b"}


def test_indoor_gym_capacity_never_exceeded(solved):
    _, result = solved
    usage = defaultdict(int)
    for e in result.entries:
        if e.resource_id == "gym":
            usage[(e.day_id, e.period_id)] += 1
    assert all(count <= 1 for count in usage.values())


def test_fixed_lesson_remains_fixed(solved):
    problem, result = solved
    for fp in problem.fixed_placements:
        matching = [
            e for e in result.entries
            if e.requirement_id == fp.requirement_id
            and e.day_id == fp.slot.day_id
            and e.period_id == fp.slot.period_id
        ]
        assert len(matching) == 1


def test_max_periods_per_day_respected(solved):
    problem, result = solved
    counts = defaultdict(int)
    for e in result.entries:
        if e.requirement_id:
            counts[(e.requirement_id, e.day_id)] += 1
    for req in problem.teaching_requirements:
        max_pd = req.distribution_policy.max_periods_per_day
        if max_pd is None:
            continue
        for day in problem.days:
            assert counts[(req.id, day.id)] <= max_pd


def test_reserved_blocks_appear_in_output(solved):
    _, result = solved
    reserved = [e for e in result.entries if e.source == EntrySource.RESERVED_BLOCK]
    assert len(reserved) == 2
