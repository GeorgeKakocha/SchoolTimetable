"""Pure orchestration/validation tests for Reserved Activities Slice
A2's `ReservedActivityService` and `ReservedActivityProjectionService`.
No database, no PostgreSQL -- all repository ports are small in-memory
fakes; real-PostgreSQL persistence/concurrency proof lives in
`tests_web/test_reserved_activity_repository.py`.

"Reserved Activity" is not a new domain entity -- it is the
user-facing name for `ReservedBlock`.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from school_timetable.application.errors import (
    ConfigurationLockedError,
    InvalidReservedActivityError,
    NonSpecialActivityTargetError,
    ReservedActivityNotFoundError,
    UnknownReferenceError,
)
from school_timetable.application.reserved_activity_models import ReservedActivityFields, ReservedActivitySlotFields
from school_timetable.application.reserved_activity_projection_service import ReservedActivityProjectionService
from school_timetable.application.reserved_activity_service import ReservedActivityService
from school_timetable.domain.activities import Activity, ActivityKind
from school_timetable.domain.blocks import ReservedBlock
from school_timetable.domain.calendar import AcademicYear, Day, Period, TimeSlot
from school_timetable.domain.groups import ClassSection, ParticipantGroup, ParticipantGroupRole
from school_timetable.domain.people import AvailabilityStatus, Teacher, TeacherAvailability
from school_timetable.domain.problem import SchedulingProblem
from school_timetable.domain.school import School
from school_timetable.fixtures.valid_fixture import build_valid_fixture

_SCHOOL = "synthetic-school"
_YEAR = "ay-2026"


def _slot(day_id: str, period_id: str) -> ReservedActivitySlotFields:
    return ReservedActivitySlotFields(day_id=day_id, period_id=period_id)


def _fields(
    special_activity_id="club_robotics", class_section_ids=("8a",), teacher_id=None, slots=(("mon", "p1"),),
) -> ReservedActivityFields:
    return ReservedActivityFields(
        special_activity_id=special_activity_id,
        class_section_ids=class_section_ids,
        teacher_id=teacher_id,
        slots=tuple(_slot(d, p) for d, p in slots),
    )


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
    genuine lock/reload-under-lock mechanics (and real persistence
    canonicalization) are proven for real against PostgreSQL in
    `tests_web/test_reserved_activity_repository.py`; this fake exists
    only to exercise `ReservedActivityService`'s own orchestration and
    error propagation."""

    def __init__(self, problem):
        self._problem = problem
        self.create_calls: list[tuple] = []
        self.update_calls: list[tuple] = []
        self.delete_calls: list[str] = []

    def create(self, school, year, reserved_activity_id, fields, validate):
        validate(self._problem)
        self.create_calls.append((reserved_activity_id, fields))
        return _fake_result(reserved_activity_id, fields)

    def update(self, school, year, reserved_activity_id, fields, validate):
        validate(self._problem)
        self.update_calls.append((reserved_activity_id, fields))
        return _fake_result(reserved_activity_id, fields)

    def delete(self, school, year, reserved_activity_id, validate):
        validate(self._problem)
        self.delete_calls.append(reserved_activity_id)


def _fake_result(reserved_activity_id, fields):
    from school_timetable.application.reserved_activity_models import ReservedActivityWriteResult
    return ReservedActivityWriteResult(
        id=reserved_activity_id,
        special_activity_id=fields.special_activity_id,
        class_section_ids=fields.class_section_ids,
        teacher_id=fields.teacher_id,
        slots=fields.slots,
    )


def _service(problem=None, active_schedule=None, id_factory=None):
    problem = problem if problem is not None else build_valid_fixture()
    write_port = _FakeWritePort(problem)
    kwargs = {} if id_factory is None else {"reserved_activity_id_factory": id_factory}
    service = ReservedActivityService(
        _FakeProblemRepository(problem), write_port, _FakeScheduleRepository(active=active_schedule), **kwargs,
    )
    return service, write_port


def _projection_service(problem=None, active_schedule=None):
    problem = problem if problem is not None else build_valid_fixture()
    return ReservedActivityProjectionService(_FakeProblemRepository(problem), _FakeScheduleRepository(active_schedule))


# == PROJECTION ===============================================================

def test_projection_special_activities_club_only_authoritative_order():
    svc = _projection_service()
    view = svc.project(_SCHOOL, _YEAR)
    assert [a.id for a in view.special_activities] == ["club_chess", "club_robotics"]


def test_projection_periods_include_all_with_is_instructional():
    svc = _projection_service()
    view = svc.project(_SCHOOL, _YEAR)
    assert len(view.periods) == 8
    assert all(p.is_instructional for p in view.periods)


def test_projection_exact_reserved_activity_item_shape():
    svc = _projection_service()
    view = svc.project(_SCHOOL, _YEAR)
    item = next(r for r in view.reserved_activities if r.id == "club_chess")
    assert item.special_activity_id == "club_chess"
    assert item.class_section_ids == ("8a", "8b")
    assert item.teacher_id is None
    assert item.slots[0].day_id == "wed"
    assert item.slots[0].period_id == "p8"


def test_projection_configuration_locked_false_by_default():
    svc = _projection_service()
    view = svc.project(_SCHOOL, _YEAR)
    assert view.configuration_locked is False


def test_projection_configuration_locked_true_when_schedule_exists():
    svc = _projection_service(active_schedule="anything-non-none")
    view = svc.project(_SCHOOL, _YEAR)
    assert view.configuration_locked is True


def test_projection_missing_configuration_propagates():
    from school_timetable.application.errors import SchedulingProblemNotFoundError

    class _RaisingProblemRepository:
        def load_by_school_and_year(self, school, year):
            raise SchedulingProblemNotFoundError(school, year)

    svc = ReservedActivityProjectionService(_RaisingProblemRepository(), _FakeScheduleRepository())
    with pytest.raises(SchedulingProblemNotFoundError):
        svc.project(_SCHOOL, _YEAR)


def test_projection_teachers_and_class_sections_authoritative_order():
    svc = _projection_service()
    view = svc.project(_SCHOOL, _YEAR)
    problem = build_valid_fixture()
    assert [t.id for t in view.teachers] == [t.id for t in problem.teachers]
    assert [c.id for c in view.class_sections] == [c.id for c in problem.class_sections]


# == CREATE ====================================================================

def test_create_valid_succeeds():
    service, write_port = _service()
    result = service.create(_SCHOOL, _YEAR, _fields())
    assert result.special_activity_id == "club_robotics"
    assert write_port.create_calls[0][0] == result.id


def test_create_generates_reserved_block_prefixed_id():
    service, _ = _service()
    result = service.create(_SCHOOL, _YEAR, _fields())
    assert result.id.startswith("reserved_block_")
    assert len(result.id) == len("reserved_block_") + 32


def test_create_uses_injected_deterministic_id_factory():
    service, write_port = _service(id_factory=lambda: "reserved_block_deterministic")
    result = service.create(_SCHOOL, _YEAR, _fields())
    assert result.id == "reserved_block_deterministic"
    assert write_port.create_calls[0][0] == "reserved_block_deterministic"


def test_create_teacher_none_valid():
    service, _ = _service()
    result = service.create(_SCHOOL, _YEAR, _fields(teacher_id=None))
    assert result.teacher_id is None


def test_create_teacher_present_valid():
    service, _ = _service()
    result = service.create(_SCHOOL, _YEAR, _fields(teacher_id="t_art"))
    assert result.teacher_id == "t_art"


def test_create_no_classes_rejected():
    service, _ = _service()
    with pytest.raises(InvalidReservedActivityError) as exc_info:
        service.create(_SCHOOL, _YEAR, _fields(class_section_ids=()))
    assert any(e.code == "RESERVED_BLOCK_REQUIRES_CLASS_SECTION" for e in exc_info.value.validation_errors)


def test_create_no_slots_rejected():
    service, _ = _service()
    with pytest.raises(InvalidReservedActivityError) as exc_info:
        service.create(_SCHOOL, _YEAR, _fields(slots=()))
    assert any(e.code == "RESERVED_BLOCK_REQUIRES_SLOT" for e in exc_info.value.validation_errors)


def test_create_duplicate_class_in_request_rejected():
    service, _ = _service()
    with pytest.raises(InvalidReservedActivityError) as exc_info:
        service.create(_SCHOOL, _YEAR, _fields(class_section_ids=("8a", "8a")))
    assert any(e.code == "DUPLICATE_RESERVED_BLOCK_CLASS_SECTION" for e in exc_info.value.validation_errors)


def test_create_duplicate_slot_in_request_rejected():
    service, _ = _service()
    with pytest.raises(InvalidReservedActivityError) as exc_info:
        service.create(_SCHOOL, _YEAR, _fields(slots=(("mon", "p1"), ("mon", "p1"))))
    assert any(e.code == "DUPLICATE_RESERVED_BLOCK_SLOT" for e in exc_info.value.validation_errors)


def test_create_unknown_special_activity():
    service, _ = _service()
    with pytest.raises(UnknownReferenceError) as exc_info:
        service.create(_SCHOOL, _YEAR, _fields(special_activity_id="no-such-activity"))
    assert exc_info.value.reference_kind == "special_activity"


def test_create_unknown_class_section():
    service, _ = _service()
    with pytest.raises(UnknownReferenceError) as exc_info:
        service.create(_SCHOOL, _YEAR, _fields(class_section_ids=("no-such-class",)))
    assert exc_info.value.reference_kind == "class_section"


def test_create_unknown_teacher():
    service, _ = _service()
    with pytest.raises(UnknownReferenceError) as exc_info:
        service.create(_SCHOOL, _YEAR, _fields(teacher_id="no-such-teacher"))
    assert exc_info.value.reference_kind == "teacher"


def test_create_unknown_day():
    service, _ = _service()
    with pytest.raises(UnknownReferenceError) as exc_info:
        service.create(_SCHOOL, _YEAR, _fields(slots=(("no-such-day", "p1"),)))
    assert exc_info.value.reference_kind == "day"


def test_create_unknown_period():
    service, _ = _service()
    with pytest.raises(UnknownReferenceError) as exc_info:
        service.create(_SCHOOL, _YEAR, _fields(slots=(("mon", "no-such-period"),)))
    assert exc_info.value.reference_kind == "period"


def test_create_wrong_kind_activity_rejected():
    service, _ = _service()
    with pytest.raises(NonSpecialActivityTargetError) as exc_info:
        service.create(_SCHOOL, _YEAR, _fields(special_activity_id="math"))
    assert exc_info.value.activity_id == "math"


def _minimal_problem_with_non_instructional_period_and_availability():
    return SchedulingProblem(
        school=School(id="s", name="S"), academic_year=AcademicYear(id="ay", label="AY"),
        days=(Day(id="d1", name="D1", index=0),),
        periods=(
            Period(id="p1", name="P1", index=0, block_id="blk", is_instructional=True),
            Period(id="p2", name="P2", index=1, block_id="blk", is_instructional=False),
        ),
        teachers=(Teacher(id="t1", first_name="T1", last_name=""),),
        class_sections=(ClassSection(id="c1", name="C1"),),
        participant_groups=(
            ParticipantGroup(id="pg1", name="PG1", class_sections=("c1",), role=ParticipantGroupRole.WHOLE_CLASS),
        ),
        activities=(Activity(id="club1", name="Club1", kind=ActivityKind.CLUB),),
        teaching_requirements=(),
        teacher_availabilities=(
            TeacherAvailability("t1", "d1", "p1", AvailabilityStatus.UNAVAILABLE),
            TeacherAvailability("t1", "d1", "p2", AvailabilityStatus.PREFER_NOT),
        ),
    )


def test_create_non_instructional_period_rejected():
    problem = _minimal_problem_with_non_instructional_period_and_availability()
    service, _ = _service(problem=problem)
    with pytest.raises(InvalidReservedActivityError) as exc_info:
        service.create("s", "ay", _fields(special_activity_id="club1", class_section_ids=("c1",), slots=(("d1", "p2"),)))
    assert any(e.code == "RESERVED_BLOCK_NON_INSTRUCTIONAL_SLOT" for e in exc_info.value.validation_errors)


def test_create_teacher_unavailable_rejected():
    problem = _minimal_problem_with_non_instructional_period_and_availability()
    service, _ = _service(problem=problem)
    with pytest.raises(InvalidReservedActivityError) as exc_info:
        service.create(
            "s", "ay",
            _fields(special_activity_id="club1", class_section_ids=("c1",), teacher_id="t1", slots=(("d1", "p1"),)),
        )
    assert any(e.code == "RESERVED_BLOCK_TEACHER_UNAVAILABLE" for e in exc_info.value.validation_errors)


def test_create_teacher_prefer_not_allowed():
    problem = _minimal_problem_with_non_instructional_period_and_availability()
    # p2 is non-instructional, so exercise PREFER_NOT via a fresh
    # instructional slot instead.
    problem = replace(
        problem,
        periods=problem.periods + (Period(id="p3", name="P3", index=2, block_id="blk", is_instructional=True),),
        teacher_availabilities=problem.teacher_availabilities + (
            TeacherAvailability("t1", "d1", "p3", AvailabilityStatus.PREFER_NOT),
        ),
    )
    service, write_port = _service(problem=problem)
    service.create(
        "s", "ay",
        _fields(special_activity_id="club1", class_section_ids=("c1",), teacher_id="t1", slots=(("d1", "p3"),)),
    )
    assert write_port.create_calls  # succeeded, no exception


def test_create_teacher_available_allowed():
    problem = _minimal_problem_with_non_instructional_period_and_availability()
    problem = replace(
        problem,
        periods=problem.periods + (Period(id="p3", name="P3", index=2, block_id="blk", is_instructional=True),),
    )
    service, write_port = _service(problem=problem)
    service.create(
        "s", "ay",
        _fields(special_activity_id="club1", class_section_ids=("c1",), teacher_id="t1", slots=(("d1", "p3"),)),
    )
    assert write_port.create_calls


def test_create_absent_availability_row_allowed():
    # Default fixture, teacher t_art has no availability row at all.
    service, write_port = _service()
    service.create(_SCHOOL, _YEAR, _fields(teacher_id="t_art"))
    assert write_port.create_calls


def test_create_class_cross_block_collision_rejected():
    service, _ = _service()
    # club_chess already claims (8a, wed, p8).
    with pytest.raises(InvalidReservedActivityError) as exc_info:
        service.create(_SCHOOL, _YEAR, _fields(class_section_ids=("8a",), slots=(("wed", "p8"),)))
    assert any(e.code == "RESERVED_BLOCK_CLASS_SLOT_COLLISION" for e in exc_info.value.validation_errors)


def test_create_teacher_cross_block_collision_rejected():
    problem = build_valid_fixture()
    problem = replace(
        problem,
        reserved_blocks=problem.reserved_blocks + (
            ReservedBlock(
                id="rb_teacher_taken", name="Taken", activity_id="club_chess",
                class_sections=("8a",),
                slots=(TimeSlot("mon", "p1"),),
                teacher_id="t_art",
            ),
        ),
    )
    service, _ = _service(problem=problem)
    with pytest.raises(InvalidReservedActivityError) as exc_info:
        service.create(
            _SCHOOL, _YEAR,
            _fields(class_section_ids=("8b",), teacher_id="t_art", slots=(("mon", "p1"),)),
        )
    assert any(e.code == "RESERVED_BLOCK_TEACHER_SLOT_COLLISION" for e in exc_info.value.validation_errors)


def test_create_rejected_once_schedule_exists():
    service, _ = _service(active_schedule="anything-non-none")
    with pytest.raises(ConfigurationLockedError):
        service.create(_SCHOOL, _YEAR, _fields())


# == UPDATE ====================================================================

def test_update_full_replacement_succeeds():
    service, write_port = _service()
    result = service.update(_SCHOOL, _YEAR, "club_chess", _fields(special_activity_id="club_chess", class_section_ids=("9a",), slots=(("mon", "p1"),)))
    assert result.class_section_ids == ("9a",)
    assert write_port.update_calls[0][0] == "club_chess"


def test_update_preserves_natural_id():
    service, _ = _service()
    result = service.update(_SCHOOL, _YEAR, "club_chess", _fields(special_activity_id="club_chess", class_section_ids=("9a",), slots=(("mon", "p1"),)))
    assert result.id == "club_chess"


def test_update_self_excluded_from_collision_check():
    # Updating club_chess to keep its own existing slot (wed/p8, classes
    # 8a/8b) must NOT be treated as a collision with itself.
    service, write_port = _service()
    service.update(
        _SCHOOL, _YEAR, "club_chess",
        _fields(special_activity_id="club_chess", class_section_ids=("8a", "8b"), slots=(("wed", "p8"),)),
    )
    assert write_port.update_calls  # succeeded, no exception


def test_update_change_activity_valid():
    service, write_port = _service()
    result = service.update(
        _SCHOOL, _YEAR, "club_chess",
        _fields(special_activity_id="club_robotics", class_section_ids=("8a",), slots=(("mon", "p1"),)),
    )
    assert result.special_activity_id == "club_robotics"


def test_update_replace_classes():
    service, write_port = _service()
    result = service.update(
        _SCHOOL, _YEAR, "club_chess",
        _fields(special_activity_id="club_chess", class_section_ids=("9a", "9b"), slots=(("mon", "p1"),)),
    )
    assert result.class_section_ids == ("9a", "9b")


def test_update_teacher_none_to_present():
    service, write_port = _service()
    result = service.update(
        _SCHOOL, _YEAR, "club_chess",
        _fields(special_activity_id="club_chess", class_section_ids=("8a",), teacher_id="t_art", slots=(("mon", "p1"),)),
    )
    assert result.teacher_id == "t_art"


def test_update_teacher_present_to_none():
    problem = build_valid_fixture()
    problem = replace(
        problem,
        reserved_blocks=problem.reserved_blocks + (
            ReservedBlock(
                id="rb_with_teacher", name="X", activity_id="club_chess",
                class_sections=("9a",), slots=(TimeSlot("tue", "p1"),), teacher_id="t_art",
            ),
        ),
    )
    service, write_port = _service(problem=problem)
    result = service.update(
        _SCHOOL, _YEAR, "rb_with_teacher",
        _fields(special_activity_id="club_chess", class_section_ids=("9a",), teacher_id=None, slots=(("tue", "p1"),)),
    )
    assert result.teacher_id is None


def test_update_replace_slots():
    service, write_port = _service()
    result = service.update(
        _SCHOOL, _YEAR, "club_chess",
        _fields(special_activity_id="club_chess", class_section_ids=("8a",), slots=(("tue", "p1"), ("tue", "p2"))),
    )
    assert set((s.day_id, s.period_id) for s in result.slots) == {("tue", "p1"), ("tue", "p2")}


def test_update_same_invariants_apply_no_classes():
    service, _ = _service()
    with pytest.raises(InvalidReservedActivityError) as exc_info:
        service.update(_SCHOOL, _YEAR, "club_chess", _fields(special_activity_id="club_chess", class_section_ids=()))
    assert any(e.code == "RESERVED_BLOCK_REQUIRES_CLASS_SECTION" for e in exc_info.value.validation_errors)


def test_update_missing_target():
    service, _ = _service()
    with pytest.raises(ReservedActivityNotFoundError):
        service.update(_SCHOOL, _YEAR, "no-such-block", _fields())


def test_update_nested_missing_special_activity_unknown_reference():
    service, _ = _service()
    with pytest.raises(UnknownReferenceError) as exc_info:
        service.update(_SCHOOL, _YEAR, "club_chess", _fields(special_activity_id="no-such-activity"))
    assert exc_info.value.reference_kind == "special_activity"


def test_update_nested_wrong_kind_non_special_activity_target():
    service, _ = _service()
    with pytest.raises(NonSpecialActivityTargetError):
        service.update(_SCHOOL, _YEAR, "club_chess", _fields(special_activity_id="math"))


def test_update_rejected_once_schedule_exists():
    service, _ = _service(active_schedule="anything-non-none")
    with pytest.raises(ConfigurationLockedError):
        service.update(_SCHOOL, _YEAR, "club_chess", _fields())


# == DELETE ====================================================================

def test_delete_valid_succeeds():
    service, write_port = _service()
    service.delete(_SCHOOL, _YEAR, "club_chess")
    assert write_port.delete_calls == ["club_chess"]


def test_delete_missing_target():
    service, _ = _service()
    with pytest.raises(ReservedActivityNotFoundError):
        service.delete(_SCHOOL, _YEAR, "no-such-block")


def test_delete_rejected_once_schedule_exists():
    service, _ = _service(active_schedule="anything-non-none")
    with pytest.raises(ConfigurationLockedError):
        service.delete(_SCHOOL, _YEAR, "club_chess")
