"""Pure orchestration tests for Phase 3A3.3's `GenerateScheduleService`
(`docs/DECISIONS.md` #31). No database, no PostgreSQL -- both
repository ports are small in-memory fakes/spies, and `run_preflight`/
`solve`/`verify` are monkeypatched at their call sites inside
`application.generate_schedule_service` so every orchestration branch
can be exercised without ever building a real CP-SAT model.

The real-PostgreSQL, real-solver, real-verifier end-to-end proof lives
in `tests_web/test_generate_schedule_service_integration.py`.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from school_timetable.application.configuration_revision_models import ConfigurationRevisionState
from school_timetable.application.errors import (
    IncompatibleLocksRequireConfirmationError,
    InvalidSchedulingConfigurationError,
    NoActiveScheduleError,
    NoConfigurationDraftError,
    ScheduleAlreadyExistsError,
    ScheduleInfeasibleError,
    StaleScheduleVersionError,
)
from school_timetable.application.generate_schedule_service import (
    GenerateScheduleService,
    ScheduleGenerationError,
    ScheduleVerificationFailedError,
)
from school_timetable.application import generate_schedule_service as svc_mod
from school_timetable.application.draft_snapshot import DraftConfigurationSnapshot
from school_timetable.application.schedule_models import ActiveScheduleVersion
from school_timetable.domain.activities import Activity
from school_timetable.domain.calendar import AcademicYear, Day, Period
from school_timetable.domain.groups import ClassSection, ParticipantGroup, ParticipantGroupRole
from school_timetable.domain.people import Teacher
from school_timetable.domain.problem import SchedulingProblem
from school_timetable.domain.requirements import (
    BlockPolicyMode,
    LessonBlockPolicy,
    PreferenceWeight,
    TeachingRequirement,
    TimePreference,
)
from school_timetable.domain.result import EntrySource, ScheduleEntry, SchedulingResult, SolverStatus
from school_timetable.domain.schedule import OccurrenceKey
from school_timetable.domain.school import School
from school_timetable.scheduling.options import SolverOptions
from school_timetable.validation.errors import ValidationError
from school_timetable.verification.verifier import VerificationReport

_CREATED_AT = datetime(2024, 1, 1, tzinfo=timezone.utc)


class _FakeProblemRepository:
    def __init__(self, problem=None):
        self._problem = problem
        self.calls: list[tuple[str, str]] = []

    def load_draft_snapshot(self, school_natural_id, academic_year_natural_id):
        self.calls.append((school_natural_id, academic_year_natural_id))
        return DraftConfigurationSnapshot(revision_id=987, problem=self._problem)

    def load_by_school_and_year(self, school_natural_id: str, academic_year_natural_id: str):
        self.calls.append((school_natural_id, academic_year_natural_id))
        return self._problem


class _FakeScheduleRepository:
    def __init__(self, active=None, persist_error: Exception | None = None):
        self._active = active
        self._persist_error = persist_error
        self.get_calls: list[tuple[str, str]] = []
        self.persist_calls: list[dict] = []
        self.regenerate_persist_calls: list[dict] = []

    def get_active_schedule(self, school_natural_id: str, academic_year_natural_id: str):
        self.get_calls.append((school_natural_id, academic_year_natural_id))
        return self._active

    def persist_initial_version(
        self,
        school_natural_id,
        academic_year_natural_id,
        problem,
        entries,
        solver_status,
        total_soft_penalty,
        wall_time_seconds,
        random_seed,
    ):
        if self._persist_error is not None:
            raise self._persist_error
        self.persist_calls.append({
            "school_natural_id": school_natural_id,
            "academic_year_natural_id": academic_year_natural_id,
            "problem": problem,
            "entries": entries,
            "solver_status": solver_status,
            "total_soft_penalty": total_soft_penalty,
            "wall_time_seconds": wall_time_seconds,
            "random_seed": random_seed,
        })
        return ActiveScheduleVersion(
            version_number=1,
            solver_status=solver_status,
            total_soft_penalty=total_soft_penalty,
            wall_time_seconds=wall_time_seconds,
            random_seed=random_seed,
            created_at=_CREATED_AT,
            entries=entries,
            locked_occurrences=frozenset(),
            configuration_revision_number=1,
        )

    def persist_regenerated_version(
        self,
        school_natural_id,
        academic_year_natural_id,
        base_version_number,
        problem,
        entries,
        locked_occurrences,
        confirmed_incompatible_lock_keys,
        solver_status,
        total_soft_penalty,
        wall_time_seconds,
        random_seed,
        *,
        expected_draft_revision_id,
    ):
        if self._persist_error is not None:
            raise self._persist_error
        self.regenerate_persist_calls.append({
            "expected_draft_revision_id": expected_draft_revision_id,
            "school_natural_id": school_natural_id,
            "academic_year_natural_id": academic_year_natural_id,
            "base_version_number": base_version_number,
            "problem": problem,
            "entries": entries,
            "locked_occurrences": locked_occurrences,
            "confirmed_incompatible_lock_keys": confirmed_incompatible_lock_keys,
            "solver_status": solver_status,
            "total_soft_penalty": total_soft_penalty,
            "wall_time_seconds": wall_time_seconds,
            "random_seed": random_seed,
        })
        new_version = ActiveScheduleVersion(
            version_number=base_version_number + 1,
            solver_status=solver_status,
            total_soft_penalty=total_soft_penalty,
            wall_time_seconds=wall_time_seconds,
            random_seed=random_seed,
            created_at=_CREATED_AT,
            entries=entries,
            locked_occurrences=locked_occurrences,
            configuration_revision_number=2,
        )
        self._active = new_version
        return new_version


class _FakeConfigurationRevisionRepository:
    """`draft_open=True` by default -- every `regenerate()` test needs an
    open draft unless it is specifically exercising the no-draft branch."""

    def __init__(self, draft_open: bool = True):
        self.draft_open = draft_open
        self.calls: list[tuple[str, str]] = []

    def get_state(self, school_natural_id: str, academic_year_natural_id: str) -> ConfigurationRevisionState:
        self.calls.append((school_natural_id, academic_year_natural_id))
        return ConfigurationRevisionState(
            published_revision_number=1,
            draft_revision_number=2 if self.draft_open else None,
            has_schedule=True,
            configuration_locked=not self.draft_open,
            timetable_out_of_date=self.draft_open,
        )


def _entries() -> tuple[ScheduleEntry, ...]:
    return (
        ScheduleEntry(
            source=EntrySource.REQUIREMENT,
            activity_id="math",
            day_id="mon",
            period_id="p1",
            class_sections=("9a",),
            teacher_id="t1",
            participant_group_id="g1",
            requirement_id="req1",
        ),
    )


def test_existing_schedule_precheck_raises_and_skips_everything(monkeypatch):
    active = ActiveScheduleVersion(
        version_number=1, solver_status=SolverStatus.OPTIMAL, total_soft_penalty=0,
        wall_time_seconds=1.0, random_seed=None, created_at=_CREATED_AT,
        entries=(), locked_occurrences=frozenset(), configuration_revision_number=1,
    )
    problem_repo = _FakeProblemRepository()
    schedule_repo = _FakeScheduleRepository(active=active)

    calls: list[str] = []
    monkeypatch.setattr(svc_mod, "run_preflight", lambda p: calls.append("preflight") or [])
    monkeypatch.setattr(svc_mod, "solve", lambda p, o: calls.append("solve"))
    monkeypatch.setattr(svc_mod, "verify", lambda p, e: calls.append("verify"))

    service = GenerateScheduleService(problem_repo, schedule_repo)
    with pytest.raises(ScheduleAlreadyExistsError) as exc_info:
        service.generate("school-1", "year-1")

    assert exc_info.value.school_natural_id == "school-1"
    assert exc_info.value.academic_year_natural_id == "year-1"
    assert schedule_repo.get_calls == [("school-1", "year-1")]
    assert problem_repo.calls == []
    assert calls == []
    assert schedule_repo.persist_calls == []


def test_invalid_preflight_raises_before_solve_or_verify(monkeypatch):
    problem = object()
    problem_repo = _FakeProblemRepository(problem=problem)
    schedule_repo = _FakeScheduleRepository(active=None)

    validation_errors = [ValidationError("BAD_THING", "something is wrong", {"k": "v"})]
    solve_calls: list = []
    verify_calls: list = []
    monkeypatch.setattr(svc_mod, "run_preflight", lambda p: validation_errors)
    monkeypatch.setattr(svc_mod, "solve", lambda p, o: solve_calls.append((p, o)))
    monkeypatch.setattr(svc_mod, "verify", lambda p, e: verify_calls.append((p, e)))

    service = GenerateScheduleService(problem_repo, schedule_repo)
    with pytest.raises(InvalidSchedulingConfigurationError) as exc_info:
        service.generate("school-1", "year-1")

    assert problem_repo.calls == [("school-1", "year-1")]
    assert solve_calls == []
    assert verify_calls == []
    assert schedule_repo.persist_calls == []
    assert exc_info.value.validation_errors == tuple(validation_errors)
    assert exc_info.value.school_natural_id == "school-1"
    assert exc_info.value.academic_year_natural_id == "year-1"


def test_infeasible_result_raises_and_skips_verify_and_persist(monkeypatch):
    problem = object()
    problem_repo = _FakeProblemRepository(problem=problem)
    schedule_repo = _FakeScheduleRepository(active=None)

    verify_calls: list = []
    monkeypatch.setattr(svc_mod, "run_preflight", lambda p: [])
    monkeypatch.setattr(
        svc_mod, "solve",
        lambda p, o: SchedulingResult(status=SolverStatus.INFEASIBLE, metadata={"wall_time_seconds": 1.0}),
    )
    monkeypatch.setattr(svc_mod, "verify", lambda p, e: verify_calls.append((p, e)))

    service = GenerateScheduleService(problem_repo, schedule_repo)
    with pytest.raises(ScheduleInfeasibleError) as exc_info:
        service.generate("school-1", "year-1")

    assert exc_info.value.school_natural_id == "school-1"
    assert exc_info.value.academic_year_natural_id == "year-1"
    assert verify_calls == []
    assert schedule_repo.persist_calls == []


def test_solver_error_raises_internal_defect_without_persisting(monkeypatch):
    problem = object()
    problem_repo = _FakeProblemRepository(problem=problem)
    schedule_repo = _FakeScheduleRepository(active=None)

    verify_calls: list = []
    monkeypatch.setattr(svc_mod, "run_preflight", lambda p: [])
    monkeypatch.setattr(
        svc_mod, "solve",
        lambda p, o: SchedulingResult(status=SolverStatus.ERROR, metadata={"error": "model builder blew up"}),
    )
    monkeypatch.setattr(svc_mod, "verify", lambda p, e: verify_calls.append((p, e)))

    service = GenerateScheduleService(problem_repo, schedule_repo)
    with pytest.raises(ScheduleGenerationError):
        service.generate("school-1", "year-1")

    assert verify_calls == []
    assert schedule_repo.persist_calls == []


def test_invalid_input_result_maps_to_invalid_configuration_without_persisting(monkeypatch):
    """Defensive path: `solve()`'s own internal preflight found errors
    even though the service's explicit preflight (patched to pass here)
    did not -- must still map to the same InvalidConfiguration outcome
    and never persist."""
    problem = object()
    problem_repo = _FakeProblemRepository(problem=problem)
    schedule_repo = _FakeScheduleRepository(active=None)

    validation_errors = (ValidationError("SOMETHING_ELSE", "also wrong", {}),)
    verify_calls: list = []
    monkeypatch.setattr(svc_mod, "run_preflight", lambda p: [])
    monkeypatch.setattr(
        svc_mod, "solve",
        lambda p, o: SchedulingResult(
            status=SolverStatus.INVALID_INPUT, validation_errors=validation_errors, metadata={},
        ),
    )
    monkeypatch.setattr(svc_mod, "verify", lambda p, e: verify_calls.append((p, e)))

    service = GenerateScheduleService(problem_repo, schedule_repo)
    with pytest.raises(InvalidSchedulingConfigurationError) as exc_info:
        service.generate("school-1", "year-1")

    assert exc_info.value.validation_errors == validation_errors
    assert verify_calls == []
    assert schedule_repo.persist_calls == []


def test_verifier_failure_raises_internal_defect_without_persisting(monkeypatch):
    problem = object()
    problem_repo = _FakeProblemRepository(problem=problem)
    schedule_repo = _FakeScheduleRepository(active=None)

    entries = _entries()
    result = SchedulingResult(
        status=SolverStatus.OPTIMAL, entries=entries, total_soft_penalty=0,
        metadata={"wall_time_seconds": 3.0},
    )
    monkeypatch.setattr(svc_mod, "run_preflight", lambda p: [])
    monkeypatch.setattr(svc_mod, "solve", lambda p, o: result)
    monkeypatch.setattr(
        svc_mod, "verify",
        lambda p, e: VerificationReport(passed=False, violations=("teacher double-booked",)),
    )

    service = GenerateScheduleService(problem_repo, schedule_repo)
    with pytest.raises(ScheduleVerificationFailedError):
        service.generate("school-1", "year-1")

    assert schedule_repo.persist_calls == []


@pytest.mark.parametrize("status", [SolverStatus.OPTIMAL, SolverStatus.FEASIBLE])
def test_successful_generation_runs_in_order_and_forwards_exact_metadata(monkeypatch, status):
    problem = object()
    problem_repo = _FakeProblemRepository(problem=problem)
    schedule_repo = _FakeScheduleRepository(active=None)

    entries = _entries()
    result = SchedulingResult(
        status=status, entries=entries, total_soft_penalty=7, metadata={"wall_time_seconds": 4.25},
    )
    call_order: list[str] = []
    monkeypatch.setattr(svc_mod, "run_preflight", lambda p: call_order.append("preflight") or [])
    monkeypatch.setattr(svc_mod, "solve", lambda p, o: call_order.append("solve") or result)
    monkeypatch.setattr(
        svc_mod, "verify",
        lambda p, e: call_order.append("verify") or VerificationReport(passed=True),
    )

    service = GenerateScheduleService(problem_repo, schedule_repo)
    returned = service.generate(
        "school-1", "year-1", solver_options=SolverOptions(random_seed=42),
    )

    assert call_order == ["preflight", "solve", "verify"]
    assert len(schedule_repo.persist_calls) == 1
    call = schedule_repo.persist_calls[0]
    assert call["school_natural_id"] == "school-1"
    assert call["academic_year_natural_id"] == "year-1"
    assert call["entries"] == entries
    assert call["solver_status"] == status
    assert call["total_soft_penalty"] == 7
    assert call["wall_time_seconds"] == 4.25
    assert call["random_seed"] == 42
    assert returned.entries == entries
    assert returned.solver_status == status


def test_persistence_race_error_propagates_unchanged_without_retry(monkeypatch):
    problem = object()
    problem_repo = _FakeProblemRepository(problem=problem)
    race_error = ScheduleAlreadyExistsError("school-1", "year-1")
    schedule_repo = _FakeScheduleRepository(active=None, persist_error=race_error)

    entries = _entries()
    result = SchedulingResult(
        status=SolverStatus.OPTIMAL, entries=entries, total_soft_penalty=0,
        metadata={"wall_time_seconds": 1.0},
    )
    monkeypatch.setattr(svc_mod, "run_preflight", lambda p: [])
    monkeypatch.setattr(svc_mod, "solve", lambda p, o: result)
    monkeypatch.setattr(svc_mod, "verify", lambda p, e: VerificationReport(passed=True))

    service = GenerateScheduleService(problem_repo, schedule_repo)
    with pytest.raises(ScheduleAlreadyExistsError) as exc_info:
        service.generate("school-1", "year-1")

    # The exact same exception instance propagates -- no catch/re-wrap,
    # no retry (get_active_schedule/solve/verify were each called once).
    assert exc_info.value is race_error
    assert schedule_repo.get_calls == [("school-1", "year-1")]


# == Safe Configuration Changes, Slice C, Checkpoint 4: regenerate() ========
#
# Orchestration-only branches (no draft, no active schedule, stale base
# version, infeasible, verification failure, argument-forwarding fidelity)
# reuse this file's existing monkeypatch discipline. `classify_locks`/
# `resolve_compatible_members`/`ProblemIndex` are never monkeypatched --
# they are cheap and pure, and exercising them for real is what proves the
# lock-confirmation contract -- so every scenario here uses a small, REAL
# `SchedulingProblem` (mirroring `tests/test_lock_compatibility.py`'s own
# fixture style), never a bare ``object()`` sentinel. The two scenarios
# that must prove a HARD pin genuinely constrains the solve (not just its
# persisted metadata) and that the ordinary generation objective -- never
# `reoptimize`'s disruption-minimizing one -- is used, run the REAL
# `scheduling.solver.solve`/`verification.verifier.verify`, unmocked.

_PREFER_Q1 = (TimePreference(preferred_periods=(0,), weight=PreferenceWeight.MEDIUM),)
_REGEN_DAYS = (Day(id="d1", name="Day1", index=0),)
_REGEN_PERIODS = (
    Period(id="q1", name="Q1", index=0, block_id="am"),
    Period(id="q2", name="Q2", index=1, block_id="am"),
)
_LOCKED_KEY = OccurrenceKey("locked_req", "d1", "q2")
_ORPHAN_KEY = OccurrenceKey("orphan_req", "d1", "q1")


def _regen_draft_problem() -> SchedulingProblem:
    """One day, two periods -- full class occupancy (Requirement 6's
    `_check_class_full_occupancy`) needs each class's own requirements to
    sum to exactly the number of instructional slots, so each of the two
    independent classes gets its own preference-bearing requirement PLUS
    a plain, no-preference filler occupying the other slot: `c1`/`pg1`
    (`locked_req` + `filler1`) and `c2`/`pg2` (`free_req` + `filler2`) --
    entirely separate teachers/groups/resources, so neither class's pair
    interacts with the other's. Both `locked_req` and `free_req` prefer
    period index 0 (``q1``). Never includes ``orphan_req`` -- a
    requirement some scenarios' historical active version locked that
    this draft has since deleted entirely, exactly the deleted-
    requirement incompatibility
    `tests/test_lock_compatibility.py::test_deleted_requirement_is_incompatible`
    already proves `classify_locks` detects."""
    return SchedulingProblem(
        school=School(id="s", name="S"), academic_year=AcademicYear(id="ay", label="AY"),
        days=_REGEN_DAYS, periods=_REGEN_PERIODS,
        teachers=(
            Teacher(id="t1", first_name="T1", last_name=""),
            Teacher(id="t1f", first_name="T1F", last_name=""),
            Teacher(id="t2", first_name="T2", last_name=""),
            Teacher(id="t2f", first_name="T2F", last_name=""),
        ),
        class_sections=(ClassSection("c1", "C1"), ClassSection("c2", "C2")),
        participant_groups=(
            ParticipantGroup("pg1", "PG1", ("c1",), ParticipantGroupRole.WHOLE_CLASS),
            ParticipantGroup("pg2", "PG2", ("c2",), ParticipantGroupRole.WHOLE_CLASS),
        ),
        activities=(
            Activity("math", "Math"), Activity("art", "Art"),
            Activity("filler1", "Filler1"), Activity("filler2", "Filler2"),
        ),
        teaching_requirements=(
            TeachingRequirement("locked_req", "t1", "math", "pg1", 1, FLEXIBLE, time_preferences=_PREFER_Q1),
            TeachingRequirement("filler1", "t1f", "filler1", "pg1", 1, FLEXIBLE),
            TeachingRequirement("free_req", "t2", "art", "pg2", 1, FLEXIBLE, time_preferences=_PREFER_Q1),
            TeachingRequirement("filler2", "t2f", "filler2", "pg2", 1, FLEXIBLE),
        ),
        resources=(), teacher_availabilities=(), reserved_blocks=(), fixed_placements=(),
    )


def _regen_entry(requirement_id, activity_id, day_id, period_id, class_sections, teacher_id, group_id):
    return ScheduleEntry(
        source=EntrySource.REQUIREMENT, activity_id=activity_id, day_id=day_id, period_id=period_id,
        class_sections=class_sections, teacher_id=teacher_id, participant_group_id=group_id,
        requirement_id=requirement_id,
    )


def _regen_active_entries(include_orphan: bool = False) -> tuple[ScheduleEntry, ...]:
    """Both preference-bearing requirements historically placed at their
    shared non-preferred slot (q2), with their class's own filler taking
    the other (q1): `locked_req`'s own lock will force the fresh solve
    back to exactly (d1, q2) despite its own soft preference for q1
    (proving the pin is a real HARD constraint, not just persisted
    metadata); `free_req` is never locked, so a normal fresh solve is
    free to move it to its preferred q1 instead (proving this path never
    minimizes disruption from this historical placement the way
    `reoptimize` would)."""
    entries = [
        _regen_entry("locked_req", "math", "d1", "q2", ("c1",), "t1", "pg1"),
        _regen_entry("filler1", "filler1", "d1", "q1", ("c1",), "t1f", "pg1"),
        _regen_entry("free_req", "art", "d1", "q2", ("c2",), "t2", "pg2"),
        _regen_entry("filler2", "filler2", "d1", "q1", ("c2",), "t2f", "pg2"),
    ]
    if include_orphan:
        entries.append(_regen_entry("orphan_req", "history", "d1", "q1", ("c3",), "t3", "pg3"))
    return tuple(entries)


def _regen_active_version(
    base_version_number: int = 1, include_orphan: bool = False, locked: bool = True,
) -> ActiveScheduleVersion:
    locked_keys: set[OccurrenceKey] = set()
    if locked:
        locked_keys.add(_LOCKED_KEY)
    if include_orphan:
        locked_keys.add(_ORPHAN_KEY)
    return ActiveScheduleVersion(
        version_number=base_version_number, solver_status=SolverStatus.OPTIMAL, total_soft_penalty=0,
        wall_time_seconds=1.0, random_seed=None, created_at=_CREATED_AT,
        entries=_regen_active_entries(include_orphan), locked_occurrences=frozenset(locked_keys),
        configuration_revision_number=1,
    )


FLEXIBLE = LessonBlockPolicy(BlockPolicyMode.FLEXIBLE)


def test_regenerate_no_draft_raises_before_touching_the_schedule():
    problem_repo = _FakeProblemRepository(problem=_regen_draft_problem())
    schedule_repo = _FakeScheduleRepository(active=_regen_active_version())
    config_repo = _FakeConfigurationRevisionRepository(draft_open=False)
    service = GenerateScheduleService(problem_repo, schedule_repo, config_repo)

    with pytest.raises(NoConfigurationDraftError) as exc_info:
        service.regenerate("school-1", "year-1", 1)

    assert exc_info.value.school_natural_id == "school-1"
    assert exc_info.value.academic_year_natural_id == "year-1"
    # No draft means the whole rest of the flow never runs.
    assert schedule_repo.get_calls == []
    assert problem_repo.calls == []
    assert schedule_repo.regenerate_persist_calls == []


def test_regenerate_no_active_schedule_raises():
    problem_repo = _FakeProblemRepository(problem=_regen_draft_problem())
    schedule_repo = _FakeScheduleRepository(active=None)
    config_repo = _FakeConfigurationRevisionRepository(draft_open=True)
    service = GenerateScheduleService(problem_repo, schedule_repo, config_repo)

    with pytest.raises(NoActiveScheduleError):
        service.regenerate("school-1", "year-1", 1)

    assert problem_repo.calls == []
    assert schedule_repo.regenerate_persist_calls == []


def test_regenerate_stale_base_version_raises_before_loading_problem():
    active = _regen_active_version(base_version_number=1)
    problem_repo = _FakeProblemRepository(problem=_regen_draft_problem())
    schedule_repo = _FakeScheduleRepository(active=active)
    config_repo = _FakeConfigurationRevisionRepository(draft_open=True)
    service = GenerateScheduleService(problem_repo, schedule_repo, config_repo)

    with pytest.raises(StaleScheduleVersionError) as exc_info:
        service.regenerate("school-1", "year-1", 999)

    assert exc_info.value.expected_base_version_number == 999
    assert exc_info.value.actual_active_version_number == 1
    # The cheap early check short-circuits before ever loading the problem.
    assert problem_repo.calls == []
    assert schedule_repo.regenerate_persist_calls == []


def test_regenerate_no_locks_runs_normal_fresh_solve_and_persists(monkeypatch):
    active = _regen_active_version(locked=False)
    problem = _regen_draft_problem()
    problem_repo = _FakeProblemRepository(problem=problem)
    schedule_repo = _FakeScheduleRepository(active=active)
    config_repo = _FakeConfigurationRevisionRepository(draft_open=True)

    entries = _entries()
    call_order: list[str] = []
    solve_calls: list[dict] = []
    monkeypatch.setattr(svc_mod, "run_preflight", lambda p: call_order.append("preflight") or [])

    def _fake_solve(p, o, hard_pins=frozenset()):
        call_order.append("solve")
        solve_calls.append({"problem": p, "options": o, "hard_pins": hard_pins})
        return SchedulingResult(
            status=SolverStatus.OPTIMAL, entries=entries, total_soft_penalty=3,
            metadata={"wall_time_seconds": 2.0},
        )

    monkeypatch.setattr(svc_mod, "solve", _fake_solve)
    monkeypatch.setattr(
        svc_mod, "verify",
        lambda p, e: call_order.append("verify") or VerificationReport(passed=True),
    )

    service = GenerateScheduleService(problem_repo, schedule_repo, config_repo)
    returned = service.regenerate("school-1", "year-1", 1)

    assert call_order == ["preflight", "solve", "verify"]
    assert solve_calls == [{"problem": problem, "options": SolverOptions(), "hard_pins": frozenset()}]
    assert len(schedule_repo.regenerate_persist_calls) == 1
    call = schedule_repo.regenerate_persist_calls[0]
    assert call["locked_occurrences"] == frozenset()
    assert call["confirmed_incompatible_lock_keys"] == frozenset()
    assert call["expected_draft_revision_id"] == 987
    assert call["base_version_number"] == 1
    assert returned.entries == entries


def test_regenerate_incompatible_locks_without_confirmation_raises(monkeypatch):
    active = _regen_active_version(include_orphan=True)
    problem_repo = _FakeProblemRepository(problem=_regen_draft_problem())
    schedule_repo = _FakeScheduleRepository(active=active)
    config_repo = _FakeConfigurationRevisionRepository(draft_open=True)

    solve_calls: list = []
    monkeypatch.setattr(svc_mod, "run_preflight", lambda p: [])
    monkeypatch.setattr(svc_mod, "solve", lambda p, o, hard_pins=frozenset(): solve_calls.append(1))
    verify_calls: list = []
    monkeypatch.setattr(svc_mod, "verify", lambda p, e: verify_calls.append(1))

    service = GenerateScheduleService(problem_repo, schedule_repo, config_repo)
    with pytest.raises(IncompatibleLocksRequireConfirmationError) as exc_info:
        service.regenerate("school-1", "year-1", 1)  # confirmed_incompatible_lock_keys defaults to empty

    assert {lock.key for lock in exc_info.value.incompatible_locks} == {_ORPHAN_KEY}
    assert solve_calls == []
    assert verify_calls == []
    assert schedule_repo.regenerate_persist_calls == []


def test_regenerate_mismatched_confirmation_set_raises(monkeypatch):
    active = _regen_active_version(include_orphan=True)
    problem_repo = _FakeProblemRepository(problem=_regen_draft_problem())
    schedule_repo = _FakeScheduleRepository(active=active)
    config_repo = _FakeConfigurationRevisionRepository(draft_open=True)

    solve_calls: list = []
    monkeypatch.setattr(svc_mod, "run_preflight", lambda p: [])
    monkeypatch.setattr(svc_mod, "solve", lambda p, o, hard_pins=frozenset(): solve_calls.append(1))
    monkeypatch.setattr(svc_mod, "verify", lambda p, e: VerificationReport(passed=True))

    service = GenerateScheduleService(problem_repo, schedule_repo, config_repo)
    # Confirms a DIFFERENT (wrong) key -- never the true orphan_req lock.
    wrong_confirmation = frozenset({_LOCKED_KEY})
    with pytest.raises(IncompatibleLocksRequireConfirmationError):
        service.regenerate("school-1", "year-1", 1, wrong_confirmation)

    assert solve_calls == []
    assert schedule_repo.regenerate_persist_calls == []


def test_regenerate_infeasible_raises_without_persisting(monkeypatch):
    active = _regen_active_version(locked=False)
    problem_repo = _FakeProblemRepository(problem=_regen_draft_problem())
    schedule_repo = _FakeScheduleRepository(active=active)
    config_repo = _FakeConfigurationRevisionRepository(draft_open=True)

    monkeypatch.setattr(svc_mod, "run_preflight", lambda p: [])
    monkeypatch.setattr(
        svc_mod, "solve",
        lambda p, o, hard_pins=frozenset(): SchedulingResult(
            status=SolverStatus.INFEASIBLE, metadata={"wall_time_seconds": 1.0},
        ),
    )
    verify_calls: list = []
    monkeypatch.setattr(svc_mod, "verify", lambda p, e: verify_calls.append(1))

    service = GenerateScheduleService(problem_repo, schedule_repo, config_repo)
    with pytest.raises(ScheduleInfeasibleError):
        service.regenerate("school-1", "year-1", 1)

    assert verify_calls == []
    assert schedule_repo.regenerate_persist_calls == []


def test_regenerate_verification_failure_raises_without_persisting(monkeypatch):
    active = _regen_active_version(locked=False)
    problem_repo = _FakeProblemRepository(problem=_regen_draft_problem())
    schedule_repo = _FakeScheduleRepository(active=active)
    config_repo = _FakeConfigurationRevisionRepository(draft_open=True)

    entries = _entries()
    monkeypatch.setattr(svc_mod, "run_preflight", lambda p: [])
    monkeypatch.setattr(
        svc_mod, "solve",
        lambda p, o, hard_pins=frozenset(): SchedulingResult(
            status=SolverStatus.OPTIMAL, entries=entries, total_soft_penalty=0,
            metadata={"wall_time_seconds": 1.0},
        ),
    )
    monkeypatch.setattr(
        svc_mod, "verify",
        lambda p, e: VerificationReport(passed=False, violations=("teacher double-booked",)),
    )

    service = GenerateScheduleService(problem_repo, schedule_repo, config_repo)
    with pytest.raises(ScheduleVerificationFailedError):
        service.regenerate("school-1", "year-1", 1)

    assert schedule_repo.regenerate_persist_calls == []


def test_regenerate_forwards_exact_solved_problem_entries_and_lock_sets_to_persist(monkeypatch):
    """Argument-forwarding fidelity, mirroring
    `test_successful_generation_runs_in_order_and_forwards_exact_metadata`:
    with one compatible lock and one confirmed-incompatible one,
    `persist_regenerated_version` must receive exactly the solved
    `problem`/`entries`, `classification.compatible_keys` (never the raw
    active version's own `locked_occurrences`), the caller's own
    `confirmed_incompatible_lock_keys`, `base_version_number`, and the
    solver's actual metadata."""
    active = _regen_active_version(include_orphan=True)
    problem = _regen_draft_problem()
    problem_repo = _FakeProblemRepository(problem=problem)
    schedule_repo = _FakeScheduleRepository(active=active)
    config_repo = _FakeConfigurationRevisionRepository(draft_open=True)

    entries = _entries()
    monkeypatch.setattr(svc_mod, "run_preflight", lambda p: [])
    monkeypatch.setattr(
        svc_mod, "solve",
        lambda p, o, hard_pins=frozenset(): SchedulingResult(
            status=SolverStatus.FEASIBLE, entries=entries, total_soft_penalty=9,
            metadata={"wall_time_seconds": 5.5},
        ),
    )
    monkeypatch.setattr(svc_mod, "verify", lambda p, e: VerificationReport(passed=True))

    monkeypatch.setattr(
        problem_repo, "load_by_school_and_year",
        lambda *args: pytest.fail("regeneration must use the paired snapshot, not an independent problem read"),
    )
    service = GenerateScheduleService(problem_repo, schedule_repo, config_repo)
    confirmed = frozenset({_ORPHAN_KEY})
    returned = service.regenerate(
        "school-1", "year-1", 1, confirmed, solver_options=SolverOptions(random_seed=7),
    )

    assert len(schedule_repo.regenerate_persist_calls) == 1
    call = schedule_repo.regenerate_persist_calls[0]
    assert call["school_natural_id"] == "school-1"
    assert call["academic_year_natural_id"] == "year-1"
    assert call["expected_draft_revision_id"] == 987
    assert call["base_version_number"] == 1
    assert call["problem"] is problem
    assert call["entries"] == entries
    assert call["locked_occurrences"] == frozenset({_LOCKED_KEY})  # never the raw active-version lock set
    assert call["confirmed_incompatible_lock_keys"] == confirmed
    assert call["solver_status"] == SolverStatus.FEASIBLE
    assert call["total_soft_penalty"] == 9
    assert call["wall_time_seconds"] == 5.5
    assert call["random_seed"] == 7
    assert returned.entries == entries


def test_regenerate_uses_normal_solve_never_reoptimize_and_pins_compatible_locks_hard():
    """REAL solve/verify, unmocked -- proves two things at once, both
    only provable with a genuine CP-SAT run:

    1. Compatible locks are actually pinned as HARD constraints: despite
       `locked_req` preferring q1, its own historical (and locked) slot
       (d2, q2) is a WORSE (non-preferred) placement -- the fresh solve
       must still place it there. Merely persisting the lock as metadata
       without wiring it into the model would let the solver move it to
       its preferred q1 instead.
    2. This path uses the ordinary generation objective, never
       `reoptimize`'s disruption-minimizing one: `free_req` is NOT
       locked, and was also historically at its own non-preferred (d2,
       q2) -- if disruption-minimization were used, it would tend to
       stay there. A normal fresh solve is free to (and does) move it to
       its preferred q1 instead.
    """
    active = _regen_active_version(locked=True)
    problem = _regen_draft_problem()
    problem_repo = _FakeProblemRepository(problem=problem)
    schedule_repo = _FakeScheduleRepository(active=active)
    config_repo = _FakeConfigurationRevisionRepository(draft_open=True)

    service = GenerateScheduleService(problem_repo, schedule_repo, config_repo)
    returned = service.regenerate("school-1", "year-1", 1, solver_options=SolverOptions(num_search_workers=1))

    locked_entry = next(e for e in returned.entries if e.requirement_id == "locked_req")
    free_entry = next(e for e in returned.entries if e.requirement_id == "free_req")
    assert (locked_entry.day_id, locked_entry.period_id) == ("d1", "q2")  # pinned despite its own preference
    assert free_entry.period_id == "q1"  # moved to its preference -- never preserved at (d2, q2)

    assert len(schedule_repo.regenerate_persist_calls) == 1
    persisted = schedule_repo.regenerate_persist_calls[0]
    assert persisted["locked_occurrences"] == frozenset({_LOCKED_KEY})
    assert persisted["confirmed_incompatible_lock_keys"] == frozenset()


def test_regenerate_exact_confirmation_pins_compatible_and_drops_incompatible():
    """REAL solve/verify: with `orphan_req`'s incompatible lock exactly
    confirmed, regeneration proceeds -- `locked_req`'s compatible lock is
    still pinned HARD, and only it (never `orphan_req`, which does not
    even exist in the draft problem) is persisted as a `LockedOccurrence`
    input."""
    active = _regen_active_version(include_orphan=True)
    problem = _regen_draft_problem()
    problem_repo = _FakeProblemRepository(problem=problem)
    schedule_repo = _FakeScheduleRepository(active=active)
    config_repo = _FakeConfigurationRevisionRepository(draft_open=True)

    service = GenerateScheduleService(problem_repo, schedule_repo, config_repo)
    returned = service.regenerate(
        "school-1", "year-1", 1, frozenset({_ORPHAN_KEY}),
        solver_options=SolverOptions(num_search_workers=1),
    )

    locked_entry = next(e for e in returned.entries if e.requirement_id == "locked_req")
    assert (locked_entry.day_id, locked_entry.period_id) == ("d1", "q2")

    persisted = schedule_repo.regenerate_persist_calls[0]
    assert persisted["locked_occurrences"] == frozenset({_LOCKED_KEY})
    assert persisted["confirmed_incompatible_lock_keys"] == frozenset({_ORPHAN_KEY})
