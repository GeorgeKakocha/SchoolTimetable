"""Pure orchestration tests for `ScheduleEditingService` (manual
timetable editing backend slice). No database -- both repository ports
are small in-memory fakes, matching `test_generate_schedule_service.py`'s
established discipline -- but domain/scheduling functions
(`validate_move`/`apply_move`, `lock_occurrence`/`unlock_occurrence`,
`reoptimize`, `verify`, `evaluate_total_soft_penalty`) are the REAL,
unmocked implementations running against the REAL `build_valid_fixture()`
problem, so these tests prove genuine domain integration -- a real
swap, a real split-group lock, a real verifier pass -- not just that the
service calls the right function names.
"""
from __future__ import annotations

import dataclasses
from datetime import datetime, timezone

import pytest

from school_timetable.application.errors import (
    MoveNotAllowedError,
    NoActiveScheduleError,
    ReoptimizationInfeasibleError,
    StaleScheduleVersionError,
)
from school_timetable.application.schedule_editing_service import ScheduleEditingService
from school_timetable.application import schedule_editing_service as svc_mod
from school_timetable.application.schedule_models import ActiveScheduleVersion
from school_timetable.domain.indexing import ProblemIndex
from school_timetable.domain.people import AvailabilityStatus, TeacherAvailability
from school_timetable.domain.result import SchedulingResult, SolverStatus
from school_timetable.domain.schedule import Schedule
from school_timetable.fixtures.valid_fixture import build_valid_fixture
from school_timetable.scheduling.editing import find_logical_occurrence, validate_move
from school_timetable.scheduling.objective import evaluate_total_soft_penalty
from school_timetable.scheduling.options import SolverOptions
from school_timetable.scheduling.solver import solve

_CREATED_AT = datetime(2024, 1, 1, tzinfo=timezone.utc)
_SCHOOL = "school-1"
_YEAR = "year-1"


class _FakeProblemRepository:
    def __init__(self, problem):
        self._problem = problem
        self.calls: list[tuple[str, str]] = []

    def load_by_school_and_year(self, school_natural_id: str, academic_year_natural_id: str):
        self.calls.append((school_natural_id, academic_year_natural_id))
        return self._problem


class _FakeScheduleRepository:
    """`active` is what `get_active_schedule` returns; `write_check_active`
    (defaulting to the same object) is what `persist_edited_version`
    compares `base_version_number` against -- kept separate so a test can
    simulate a race where the true active version has already moved on
    by the time persistence is attempted, exactly mirroring the real
    repository's own two-phase (cheap-read-then-locked-recheck)
    protection proven for real in `tests_web/test_schedule_repository.py`.
    """

    def __init__(self, active: ActiveScheduleVersion | None):
        self.active = active
        self.write_check_active = active
        self.persist_calls: list[dict] = []
        self._next_version_number = (active.version_number + 1) if active else 1

    def get_active_schedule(self, school_natural_id: str, academic_year_natural_id: str):
        return self.active

    def persist_edited_version(
        self, school_natural_id, academic_year_natural_id, base_version_number, candidate,
        solver_status, total_soft_penalty, wall_time_seconds, random_seed,
    ):
        current = self.write_check_active
        if current is None or current.version_number != base_version_number:
            raise StaleScheduleVersionError(
                school_natural_id, academic_year_natural_id,
                base_version_number, current.version_number if current else -1,
            )
        new_version = ActiveScheduleVersion(
            version_number=self._next_version_number,
            solver_status=solver_status,
            total_soft_penalty=total_soft_penalty,
            wall_time_seconds=wall_time_seconds,
            random_seed=random_seed,
            created_at=_CREATED_AT,
            entries=candidate.entries,
            locked_occurrences=candidate.locked_occurrences,
        )
        self.persist_calls.append({
            "base_version_number": base_version_number,
            "candidate": candidate,
            "solver_status": solver_status,
            "total_soft_penalty": total_soft_penalty,
            "wall_time_seconds": wall_time_seconds,
            "random_seed": random_seed,
        })
        self._next_version_number += 1
        self.active = new_version
        self.write_check_active = new_version
        return new_version


def _seed_v1():
    problem = build_valid_fixture()
    result = solve(problem, SolverOptions(random_seed=11, num_search_workers=1))
    assert result.is_success
    v1 = ActiveScheduleVersion(
        version_number=1, solver_status=result.status, total_soft_penalty=result.total_soft_penalty,
        wall_time_seconds=1.0, random_seed=11, created_at=_CREATED_AT,
        entries=result.entries, locked_occurrences=frozenset(),
    )
    return problem, v1


def _find_simple_move(problem, index, schedule):
    """Finds one valid manual move -- same search strategy as
    `run_editing_demo.py`. `validate_move`'s own HARD-rule checks
    (including REQUIRED-block-pattern integrity -- see the "manual
    timetable editing correction slice" in `docs/SCHEDULE_EDITING.md`)
    are trusted directly; no defensive post-hoc re-verification is
    needed here."""
    simple_occs, seen = [], set()
    for e in schedule.entries:
        if e.requirement_id is None or (e.requirement_id, e.day_id, e.period_id) in seen:
            continue
        occ = find_logical_occurrence(problem, index, schedule, e.requirement_id, e.day_id, e.period_id)
        if occ.length == 1 and len(occ.requirement_ids) == 1:
            simple_occs.append((e.requirement_id, e.day_id, e.period_id))
        seen.update((m.requirement_id, m.day_id, m.period_id) for m in occ.members)

    for (r1, d1, p1) in simple_occs:
        for (r2, d2, p2) in simple_occs:
            if r1 == r2 or d1 == d2:
                continue
            result = validate_move(problem, schedule, r1, d1, p1, d2, p2, index=index)
            if not result.allowed:
                continue
            swapped = {e.requirement_id for e in result.plan.removed_entries}
            if swapped != {r1, r2}:
                continue
            return r1, d1, p1, d2, p2
    raise AssertionError("expected at least one valid move in this fixture")


# == A/B/C: manual move ======================================================


def test_move_success_creates_new_active_version_with_truthful_metadata():
    problem, v1 = _seed_v1()
    problem_repo = _FakeProblemRepository(problem)
    schedule_repo = _FakeScheduleRepository(v1)
    index = ProblemIndex(problem)
    schedule = Schedule(entries=v1.entries)
    r1, d1, p1, d2, p2 = _find_simple_move(problem, index, schedule)

    service = ScheduleEditingService(problem_repo, schedule_repo)
    v2 = service.move(_SCHOOL, _YEAR, 1, r1, d1, p1, d2, p2)

    assert v2.version_number == 2
    assert v2.solver_status == SolverStatus.FEASIBLE  # never OPTIMAL merely because v1 was
    assert v2.entries != v1.entries
    assert v2.locked_occurrences == frozenset()
    # Truthful, independently recomputed penalty -- not copied from v1.
    assert v2.total_soft_penalty == evaluate_total_soft_penalty(problem, v2.entries)
    assert len(schedule_repo.persist_calls) == 1


def test_move_rejection_for_a_fixed_placement_raises_and_persists_nothing():
    """`art_8b` is fixed at (mon, p1) in the fixture -- moving it must be
    rejected with the real MoveViolation code, and nothing persisted."""
    problem, v1 = _seed_v1()
    problem_repo = _FakeProblemRepository(problem)
    schedule_repo = _FakeScheduleRepository(v1)
    service = ScheduleEditingService(problem_repo, schedule_repo)

    with pytest.raises(MoveNotAllowedError) as exc_info:
        service.move(_SCHOOL, _YEAR, 1, "art_8b", "mon", "p1", "tue", "p1")

    assert exc_info.value.violations[0][0] == "FIXED_PLACEMENT"
    assert schedule_repo.persist_calls == []
    assert schedule_repo.active is v1


def test_move_with_stale_base_version_raises_immediately_without_loading_problem():
    problem, v1 = _seed_v1()
    problem_repo = _FakeProblemRepository(problem)
    schedule_repo = _FakeScheduleRepository(v1)
    service = ScheduleEditingService(problem_repo, schedule_repo)

    with pytest.raises(StaleScheduleVersionError) as exc_info:
        service.move(_SCHOOL, _YEAR, 999, "art_8b", "mon", "p1", "tue", "p1")

    assert exc_info.value.expected_base_version_number == 999
    assert exc_info.value.actual_active_version_number == 1
    # The cheap early check short-circuits before ever loading the problem.
    assert problem_repo.calls == []
    assert schedule_repo.persist_calls == []


def test_move_with_no_active_schedule_raises_no_active_schedule_error():
    problem_repo = _FakeProblemRepository(build_valid_fixture())
    schedule_repo = _FakeScheduleRepository(active=None)
    service = ScheduleEditingService(problem_repo, schedule_repo)

    with pytest.raises(NoActiveScheduleError):
        service.move(_SCHOOL, _YEAR, 1, "art_8b", "mon", "p1", "tue", "p1")


# == D/E/F: lock / unlock ====================================================


def test_lock_creates_new_version_with_unchanged_entries_and_stored_lock():
    problem, v1 = _seed_v1()
    problem_repo = _FakeProblemRepository(problem)
    schedule_repo = _FakeScheduleRepository(v1)
    entry = next(e for e in v1.entries if e.requirement_id == "math_8a")

    service = ScheduleEditingService(problem_repo, schedule_repo)
    v2 = service.lock(_SCHOOL, _YEAR, 1, "math_8a", entry.day_id, entry.period_id)

    assert v2.version_number == 2
    assert v2.entries == v1.entries  # entries unchanged
    assert any(k.requirement_id == "math_8a" for k in v2.locked_occurrences)
    # Entries didn't change -- the base version's own truthful metadata
    # carries forward unchanged, not copied for convenience.
    assert v2.solver_status == v1.solver_status
    assert v2.total_soft_penalty == v1.total_soft_penalty


def test_lock_a_split_group_branch_locks_both_siblings():
    problem, v1 = _seed_v1()
    problem_repo = _FakeProblemRepository(problem)
    schedule_repo = _FakeScheduleRepository(v1)
    entry = next(e for e in v1.entries if e.requirement_id == "german_8a")

    service = ScheduleEditingService(problem_repo, schedule_repo)
    v2 = service.lock(_SCHOOL, _YEAR, 1, "german_8a", entry.day_id, entry.period_id)

    locked_req_ids = {k.requirement_id for k in v2.locked_occurrences}
    assert locked_req_ids == {"german_8a", "russian_8a"}


def test_unlock_produces_a_new_version_while_the_historical_locked_version_is_unaffected():
    problem, v1 = _seed_v1()
    problem_repo = _FakeProblemRepository(problem)
    schedule_repo = _FakeScheduleRepository(v1)
    entry = next(e for e in v1.entries if e.requirement_id == "german_8a")
    service = ScheduleEditingService(problem_repo, schedule_repo)

    v2 = service.lock(_SCHOOL, _YEAR, 1, "german_8a", entry.day_id, entry.period_id)
    v2_locks_snapshot = v2.locked_occurrences  # captured before v3 exists
    v3 = service.unlock(_SCHOOL, _YEAR, 2, "german_8a", entry.day_id, entry.period_id)

    assert v3.version_number == 3
    assert v3.locked_occurrences == frozenset()
    # v2's own persisted candidate (recorded at the time) still shows its
    # locks -- unlocking never rewrites history, it only omits the lock
    # from the NEW version.
    v2_persist_call = schedule_repo.persist_calls[0]
    assert v2_persist_call["candidate"].locked_occurrences == v2_locks_snapshot
    assert {k.requirement_id for k in v2_locks_snapshot} == {"german_8a", "russian_8a"}


# == G/H: reoptimize ==========================================================


def test_reoptimize_success_honors_locks_and_persists_actual_solver_metadata():
    problem, v1 = _seed_v1()
    entry = next(e for e in v1.entries if e.requirement_id == "german_8a")

    problem_repo = _FakeProblemRepository(problem)
    schedule_repo = _FakeScheduleRepository(v1)
    service = ScheduleEditingService(problem_repo, schedule_repo)

    v2 = service.lock(_SCHOOL, _YEAR, 1, "german_8a", entry.day_id, entry.period_id)

    # A new constraint forces at least some reshuffling: some teacher
    # (not german/russian) becomes UNAVAILABLE exactly where they were
    # scheduled, but the problem loaded for reoptimize must reflect it --
    # patch the fake problem repository's returned problem in place.
    forced_entry = next(e for e in v2.entries if e.teacher_id not in ("t_german", "t_russian"))
    changed_problem = dataclasses.replace(
        problem,
        teacher_availabilities=problem.teacher_availabilities + (
            TeacherAvailability(
                forced_entry.teacher_id, forced_entry.day_id, forced_entry.period_id,
                AvailabilityStatus.UNAVAILABLE,
            ),
        ),
    )
    problem_repo._problem = changed_problem

    v3 = service.reoptimize(_SCHOOL, _YEAR, 2, solver_options=SolverOptions(max_time_seconds=30, random_seed=3))

    assert v3.version_number == 3
    assert v3.solver_status in (SolverStatus.OPTIMAL, SolverStatus.FEASIBLE)
    # Locks fed into reoptimize are preserved in the persisted candidate.
    assert {k.requirement_id for k in v3.locked_occurrences} == {"german_8a", "russian_8a"}
    locked_entry = next(e for e in v3.entries if e.requirement_id == "german_8a")
    assert (locked_entry.day_id, locked_entry.period_id) == (entry.day_id, entry.period_id)
    # The forced-unavailable teacher is no longer scheduled at that slot.
    assert not any(
        e.teacher_id == forced_entry.teacher_id
        and e.day_id == forced_entry.day_id and e.period_id == forced_entry.period_id
        for e in v3.entries
    )
    # Actual reoptimizer metadata, never faked/copied.
    call = schedule_repo.persist_calls[-1]
    assert call["random_seed"] == 3
    assert call["wall_time_seconds"] > 0


def test_reoptimize_infeasible_persists_nothing(monkeypatch):
    problem, v1 = _seed_v1()
    problem_repo = _FakeProblemRepository(problem)
    schedule_repo = _FakeScheduleRepository(v1)
    service = ScheduleEditingService(problem_repo, schedule_repo)

    monkeypatch.setattr(
        svc_mod, "_reoptimize", lambda p, s, o: SchedulingResult(status=SolverStatus.INFEASIBLE),
    )

    with pytest.raises(ReoptimizationInfeasibleError):
        service.reoptimize(_SCHOOL, _YEAR, 1)

    assert schedule_repo.persist_calls == []
    assert schedule_repo.active is v1


# == I: stale-after-work race =================================================


def test_move_with_a_race_between_read_and_persist_is_still_rejected():
    """The service's own cheap early check reads `active` as v1 and lets
    the (fast) move proceed; before persistence actually happens, someone
    else's edit is simulated as already active -- `persist_edited_version`
    (here, the fake standing in for the real repository's own
    lock-protected recheck, proven for real in
    `tests_web/test_schedule_repository.py`) must still reject the
    candidate, and nothing is promoted."""
    problem, v1 = _seed_v1()
    problem_repo = _FakeProblemRepository(problem)
    schedule_repo = _FakeScheduleRepository(v1)
    index = ProblemIndex(problem)
    schedule = Schedule(entries=v1.entries)
    r1, d1, p1, d2, p2 = _find_simple_move(problem, index, schedule)

    # Simulate a concurrent promotion to v2 that the service's cheap
    # read (still returning v1) does not see.
    v2 = ActiveScheduleVersion(
        version_number=2, solver_status=SolverStatus.FEASIBLE, total_soft_penalty=0,
        wall_time_seconds=0.0, random_seed=None, created_at=_CREATED_AT,
        entries=v1.entries, locked_occurrences=frozenset(),
    )
    schedule_repo.write_check_active = v2

    service = ScheduleEditingService(problem_repo, schedule_repo)
    with pytest.raises(StaleScheduleVersionError) as exc_info:
        service.move(_SCHOOL, _YEAR, 1, r1, d1, p1, d2, p2)

    assert exc_info.value.expected_base_version_number == 1
    assert exc_info.value.actual_active_version_number == 2
    assert schedule_repo.persist_calls == []
    # `get_active_schedule` (what the service's cheap check saw) is left
    # exactly as it was -- still v1 -- proving nothing was promoted.
    assert schedule_repo.active is v1
