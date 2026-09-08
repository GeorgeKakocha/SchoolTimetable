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

from school_timetable.application.errors import (
    InvalidSchedulingConfigurationError,
    ScheduleAlreadyExistsError,
    ScheduleInfeasibleError,
)
from school_timetable.application.generate_schedule_service import (
    GenerateScheduleService,
    ScheduleGenerationError,
    ScheduleVerificationFailedError,
)
from school_timetable.application import generate_schedule_service as svc_mod
from school_timetable.application.schedule_models import ActiveScheduleVersion
from school_timetable.domain.result import EntrySource, ScheduleEntry, SchedulingResult, SolverStatus
from school_timetable.scheduling.options import SolverOptions
from school_timetable.validation.errors import ValidationError
from school_timetable.verification.verifier import VerificationReport

_CREATED_AT = datetime(2024, 1, 1, tzinfo=timezone.utc)


class _FakeProblemRepository:
    def __init__(self, problem=None):
        self._problem = problem
        self.calls: list[tuple[str, str]] = []

    def load_by_school_and_year(self, school_natural_id: str, academic_year_natural_id: str):
        self.calls.append((school_natural_id, academic_year_natural_id))
        return self._problem


class _FakeScheduleRepository:
    def __init__(self, active=None, persist_error: Exception | None = None):
        self._active = active
        self._persist_error = persist_error
        self.get_calls: list[tuple[str, str]] = []
        self.persist_calls: list[dict] = []

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
        entries=(), locked_occurrences=frozenset(),
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
