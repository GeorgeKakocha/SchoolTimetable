"""Pure orchestration/validation tests for Phase 3C.2's
`TeachingAssignmentService` (`docs/DECISIONS.md` #34, #36). No
database, no PostgreSQL -- all three repository ports are small
in-memory fakes; real-PostgreSQL persistence/concurrency proof lives in
`tests_web/test_teaching_assignment_repository.py`.
"""
from __future__ import annotations

import pytest

from school_timetable.application.errors import (
    AdvancedRequirementNotEditableError,
    ConfigurationLockedError,
    DuplicateTeachingAssignmentError,
    NonWholeClassTargetError,
    TeachingAssignmentNotFoundError,
    UnknownReferenceError,
)
from school_timetable.application.teaching_assignment_models import TeachingAssignmentFields
from school_timetable.application.teaching_assignment_service import TeachingAssignmentService
from school_timetable.fixtures.valid_fixture import build_valid_fixture

_SCHOOL = "synthetic-school"
_YEAR = "ay-2026"


class _FakeProblemRepository:
    def __init__(self, problem):
        self._problem = problem

    def load_by_school_and_year(self, school_natural_id, academic_year_natural_id):
        return self._problem


class _FakeScheduleRepository:
    def __init__(self, active=None):
        self._active = active

    def get_active_schedule(self, school_natural_id, academic_year_natural_id):
        return self._active


class _FakeWritePort:
    """Simulates the authoritative, lock-protected recheck by simply
    re-invoking `validate` against the same (unchanged) problem -- the
    genuine lock/reload-under-lock mechanics are proven for real against
    PostgreSQL in `tests_web/test_teaching_assignment_repository.py`;
    this fake exists only to exercise `TeachingAssignmentService`'s own
    orchestration and error propagation."""

    def __init__(self, problem):
        self._problem = problem
        self.create_calls: list[tuple] = []
        self.update_calls: list[tuple] = []
        self.delete_calls: list[tuple] = []

    def create(self, school, year, natural_id, teacher_id, group_id, activity_id, weekly_periods, validate):
        validate(self._problem)
        self.create_calls.append((natural_id, teacher_id, group_id, activity_id, weekly_periods))

    def update(self, school, year, natural_id, teacher_id, group_id, activity_id, weekly_periods, validate):
        validate(self._problem)
        self.update_calls.append((natural_id, teacher_id, group_id, activity_id, weekly_periods))

    def delete(self, school, year, natural_id, validate):
        validate(self._problem)
        self.delete_calls.append(natural_id)


def _service(problem=None, active_schedule=None):
    problem = problem if problem is not None else build_valid_fixture()
    write_port = _FakeWritePort(problem)
    service = TeachingAssignmentService(
        _FakeProblemRepository(problem),
        write_port,
        _FakeScheduleRepository(active=active_schedule),
    )
    return service, write_port


# -- CREATE ---------------------------------------------------------------

def test_create_plain_whole_class_succeeds():
    # (t_history, pg_9a, history) has no existing requirement -- history
    # is only otherwise assigned to pg_8a, pg_8b, and the 9a+9b merged
    # group, never to pg_9a alone -- so this is a genuinely new, plain,
    # WHOLE_CLASS assignment, not a duplicate of an existing fixture row.
    service, write_port = _service()
    result = service.create(
        _SCHOOL, _YEAR, TeachingAssignmentFields(
            teacher_id="t_history", participant_group_id="pg_9a", activity_id="history", weekly_periods=3,
        ),
    )
    assert result.natural_id in {c[0] for c in write_port.create_calls}


def test_create_generates_an_opaque_backend_natural_id():
    service, _ = _service()
    result = service.create(
        _SCHOOL, _YEAR, TeachingAssignmentFields(
            teacher_id="t_history", participant_group_id="pg_9a", activity_id="history", weekly_periods=3,
        ),
    )
    assert result.natural_id.startswith("req_")
    assert len(result.natural_id) == len("req_") + 32  # full uuid4 hex, never truncated


def test_create_uses_an_injected_id_factory_deterministically():
    problem = build_valid_fixture()
    write_port = _FakeWritePort(problem)
    service = TeachingAssignmentService(
        _FakeProblemRepository(problem), write_port, _FakeScheduleRepository(),
        id_factory=lambda: "req_deterministic_test_id",
    )
    result = service.create(
        _SCHOOL, _YEAR, TeachingAssignmentFields(
            teacher_id="t_history", participant_group_id="pg_9a", activity_id="history", weekly_periods=3,
        ),
    )
    assert result.natural_id == "req_deterministic_test_id"


def test_create_weekly_periods_zero_or_negative_rejected():
    service, _ = _service()
    with pytest.raises(Exception):  # InvalidTeachingAssignmentError
        service.create(
            _SCHOOL, _YEAR, TeachingAssignmentFields(
                teacher_id="t_art", participant_group_id="pg_9b", activity_id="art", weekly_periods=0,
            ),
        )


def test_create_unknown_teacher_rejected():
    service, _ = _service()
    with pytest.raises(UnknownReferenceError):
        service.create(
            _SCHOOL, _YEAR, TeachingAssignmentFields(
                teacher_id="t_nobody", participant_group_id="pg_9b", activity_id="art", weekly_periods=3,
            ),
        )


def test_create_unknown_activity_rejected():
    service, _ = _service()
    with pytest.raises(UnknownReferenceError):
        service.create(
            _SCHOOL, _YEAR, TeachingAssignmentFields(
                teacher_id="t_art", participant_group_id="pg_9b", activity_id="no-such-activity", weekly_periods=3,
            ),
        )


def test_create_unknown_participant_group_rejected():
    service, _ = _service()
    with pytest.raises(UnknownReferenceError):
        service.create(
            _SCHOOL, _YEAR, TeachingAssignmentFields(
                teacher_id="t_art", participant_group_id="no-such-group", activity_id="art", weekly_periods=3,
            ),
        )


def test_create_subgroup_target_rejected():
    service, _ = _service()
    with pytest.raises(NonWholeClassTargetError):
        service.create(
            _SCHOOL, _YEAR, TeachingAssignmentFields(
                teacher_id="t_german", participant_group_id="pg_8a_german", activity_id="german", weekly_periods=3,
            ),
        )


def test_create_merged_classes_target_rejected():
    service, _ = _service()
    with pytest.raises(NonWholeClassTargetError):
        service.create(
            _SCHOOL, _YEAR, TeachingAssignmentFields(
                teacher_id="t_history", participant_group_id="pg_9a_9b_merged",
                activity_id="history", weekly_periods=1,
            ),
        )


def test_create_duplicate_rejected():
    # math_8a already assigns t_math/pg_8a/math.
    service, _ = _service()
    with pytest.raises(DuplicateTeachingAssignmentError):
        service.create(
            _SCHOOL, _YEAR, TeachingAssignmentFields(
                teacher_id="t_math", participant_group_id="pg_8a", activity_id="math", weekly_periods=5,
            ),
        )


# -- UPDATE -----------------------------------------------------------------

def test_update_plain_succeeds_and_preserves_natural_id():
    # science_8a is plain (FLEXIBLE, no split/resource/time-pref, WHOLE_CLASS).
    service, write_port = _service()
    result = service.update(
        _SCHOOL, _YEAR, "science_8a", TeachingAssignmentFields(
            teacher_id="t_science", participant_group_id="pg_8a", activity_id="science", weekly_periods=7,
        ),
    )
    assert result.natural_id == "science_8a"
    assert write_port.update_calls[0][0] == "science_8a"


def test_update_not_found_rejected():
    service, _ = _service()
    with pytest.raises(TeachingAssignmentNotFoundError):
        service.update(
            _SCHOOL, _YEAR, "does-not-exist", TeachingAssignmentFields(
                teacher_id="t_science", participant_group_id="pg_8a", activity_id="science", weekly_periods=7,
            ),
        )


def test_update_duplicate_rejected_excluding_self():
    # Updating science_8a to target math_8a's exact (teacher, group,
    # activity) triple is a real duplicate; updating it to its OWN
    # unchanged triple must NOT be rejected as a duplicate of itself.
    service, _ = _service()
    with pytest.raises(DuplicateTeachingAssignmentError):
        service.update(
            _SCHOOL, _YEAR, "science_8a", TeachingAssignmentFields(
                teacher_id="t_math", participant_group_id="pg_8a", activity_id="math", weekly_periods=5,
            ),
        )

    service2, _ = _service()
    result = service2.update(
        _SCHOOL, _YEAR, "science_8a", TeachingAssignmentFields(
            teacher_id="t_science", participant_group_id="pg_8a", activity_id="science", weekly_periods=9,
        ),
    )
    assert result.natural_id == "science_8a"


def test_update_changing_target_to_subgroup_rejected():
    service, _ = _service()
    with pytest.raises(NonWholeClassTargetError):
        service.update(
            _SCHOOL, _YEAR, "science_8a", TeachingAssignmentFields(
                teacher_id="t_science", participant_group_id="pg_8a_german", activity_id="science", weekly_periods=7,
            ),
        )


def test_update_advanced_block_policy_requirement_rejected():
    # math_8a has a REQUIRED block policy.
    service, _ = _service()
    with pytest.raises(AdvancedRequirementNotEditableError) as exc_info:
        service.update(
            _SCHOOL, _YEAR, "math_8a", TeachingAssignmentFields(
                teacher_id="t_math", participant_group_id="pg_8a", activity_id="math", weekly_periods=5,
            ),
        )
    assert "block_policy" in exc_info.value.reasons


def test_update_advanced_distribution_requirement_rejected():
    # history_8a has a non-default distribution_policy.
    service, _ = _service()
    with pytest.raises(AdvancedRequirementNotEditableError) as exc_info:
        service.update(
            _SCHOOL, _YEAR, "history_8a", TeachingAssignmentFields(
                teacher_id="t_history", participant_group_id="pg_8a", activity_id="history", weekly_periods=9,
            ),
        )
    assert "distribution_policy" in exc_info.value.reasons or "time_preferences" in exc_info.value.reasons


def test_update_resource_requirement_rejected():
    # sport_8a has a resource_requirement (the gym).
    service, _ = _service()
    with pytest.raises(AdvancedRequirementNotEditableError) as exc_info:
        service.update(
            _SCHOOL, _YEAR, "sport_8a", TeachingAssignmentFields(
                teacher_id="t_sport", participant_group_id="pg_8a", activity_id="sport", weekly_periods=2,
            ),
        )
    assert "resource_requirement" in exc_info.value.reasons


def test_update_split_requirement_rejected():
    # german_8a has a split_group_id and targets a SUBGROUP.
    service, _ = _service()
    with pytest.raises(AdvancedRequirementNotEditableError) as exc_info:
        service.update(
            _SCHOOL, _YEAR, "german_8a", TeachingAssignmentFields(
                teacher_id="t_german", participant_group_id="pg_8a_german", activity_id="german", weekly_periods=3,
            ),
        )
    assert "split_group_id" in exc_info.value.reasons


def test_update_requirement_with_fixed_placement_rejected():
    # art_8b has a FixedPlacement (fixed_art_8b).
    service, _ = _service()
    with pytest.raises(AdvancedRequirementNotEditableError) as exc_info:
        service.update(
            _SCHOOL, _YEAR, "art_8b", TeachingAssignmentFields(
                teacher_id="t_art", participant_group_id="pg_8b", activity_id="art", weekly_periods=9,
            ),
        )
    assert "fixed_placement" in exc_info.value.reasons


# -- DELETE -----------------------------------------------------------------

def test_delete_plain_succeeds():
    service, write_port = _service()
    service.delete(_SCHOOL, _YEAR, "science_8a")
    assert write_port.delete_calls == ["science_8a"]


def test_delete_not_found_rejected():
    service, _ = _service()
    with pytest.raises(TeachingAssignmentNotFoundError):
        service.delete(_SCHOOL, _YEAR, "does-not-exist")


def test_delete_advanced_requirement_rejected():
    service, _ = _service()
    with pytest.raises(AdvancedRequirementNotEditableError):
        service.delete(_SCHOOL, _YEAR, "math_8a")


def test_delete_requirement_with_fixed_placement_rejected():
    service, _ = _service()
    with pytest.raises(AdvancedRequirementNotEditableError) as exc_info:
        service.delete(_SCHOOL, _YEAR, "art_8b")
    assert "fixed_placement" in exc_info.value.reasons


# -- LOCK ---------------------------------------------------------------

def test_create_rejected_once_schedule_exists():
    service, _ = _service(active_schedule="anything-non-none")
    with pytest.raises(ConfigurationLockedError):
        service.create(
            _SCHOOL, _YEAR, TeachingAssignmentFields(
                teacher_id="t_history", participant_group_id="pg_9a", activity_id="history", weekly_periods=3,
            ),
        )


def test_update_rejected_once_schedule_exists():
    service, _ = _service(active_schedule="anything-non-none")
    with pytest.raises(ConfigurationLockedError):
        service.update(
            _SCHOOL, _YEAR, "science_8a", TeachingAssignmentFields(
                teacher_id="t_science", participant_group_id="pg_8a", activity_id="science", weekly_periods=9,
            ),
        )


def test_delete_rejected_once_schedule_exists():
    service, _ = _service(active_schedule="anything-non-none")
    with pytest.raises(ConfigurationLockedError):
        service.delete(_SCHOOL, _YEAR, "science_8a")


# -- VALIDATION (save-time boundary) ----------------------------------------

def test_incremental_class_occupancy_incompleteness_does_not_prevent_a_legitimate_save():
    # Deleting science_8a leaves class 8a under-occupied (a
    # CLASS_OCCUPANCY_MISMATCH), which must surface only as a warning,
    # never block the save -- a school mid-configuration is expected to
    # pass through such incomplete states.
    service, _ = _service()
    warnings = service.delete(_SCHOOL, _YEAR, "science_8a")
    assert any(w.code == "CLASS_OCCUPANCY_MISMATCH" for w in warnings)
