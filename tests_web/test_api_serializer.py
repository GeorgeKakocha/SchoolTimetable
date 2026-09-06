"""Pure, DB-free unit tests for the domain/application -> API serializer
(Phase 3A2.4, extended Phase 3A3.4/3B.1; `api/serializer.py`). No
database, no FastAPI TestClient -- these isolate serialization failures
from repository/DB failures, which
`test_config_api.py`/`test_schedule_api.py`/`test_class_timetable_api.py`
cover separately."""
from __future__ import annotations

from datetime import datetime, timezone

from school_timetable.api.schemas import ActiveScheduleResponse, GenerateScheduleResponse, ScheduleEntryResponse
from school_timetable.api.serializer import (
    active_schedule_response_from_active_version,
    class_timetable_response_from_view,
    config_response_from_problem,
    generate_response_from_active_version,
    schedule_entry_response_from_entry,
    validation_diagnostic_response_from_error,
)
from school_timetable.application.class_timetable_models import (
    ClassTimetableCell,
    ClassTimetableEntry,
    ClassTimetableRow,
    ClassTimetableView,
    DayHeader,
)
from school_timetable.application.schedule_models import ActiveScheduleVersion
from school_timetable.domain.result import EntrySource, ScheduleEntry, SolverStatus
from school_timetable.fixtures.valid_fixture import build_valid_fixture
from school_timetable.validation.errors import ValidationError

_CREATED_AT = datetime(2024, 1, 1, tzinfo=timezone.utc)


def test_fixture_problem_converts_successfully_with_exact_top_level_shape():
    problem = build_valid_fixture()
    response = config_response_from_problem(problem)

    assert response.school.id == problem.school.id
    assert response.academic_year.id == problem.academic_year.id
    assert len(response.days) == len(problem.days)
    assert len(response.periods) == len(problem.periods)
    assert len(response.teachers) == len(problem.teachers)
    assert len(response.class_sections) == len(problem.class_sections)
    assert len(response.participant_groups) == len(problem.participant_groups)
    assert len(response.activities) == len(problem.activities)
    assert len(response.teaching_requirements) == len(problem.teaching_requirements)
    assert len(response.resources) == len(problem.resources)
    assert len(response.teacher_availabilities) == len(problem.teacher_availabilities)
    assert len(response.reserved_blocks) == len(problem.reserved_blocks)
    assert len(response.fixed_placements) == len(problem.fixed_placements)


def test_enums_serialize_as_expected_json_strings():
    problem = build_valid_fixture()
    dumped = config_response_from_problem(problem).model_dump(mode="json")

    math_8a = next(r for r in dumped["teaching_requirements"] if r["id"] == "math_8a")
    assert math_8a["block_policy"]["mode"] == "REQUIRED"

    science_8b = next(r for r in dumped["teaching_requirements"] if r["id"] == "science_8b")
    assert science_8b["block_policy"]["mode"] == "PREFERRED"

    history_8a = next(r for r in dumped["teaching_requirements"] if r["id"] == "history_8a")
    assert history_8a["time_preferences"][0]["weight"] == "MEDIUM"

    unavailable = next(a for a in dumped["teacher_availabilities"] if a["status"] == "UNAVAILABLE")
    assert unavailable["teacher_id"] == "t_science"

    prefer_not = next(a for a in dumped["teacher_availabilities"] if a["status"] == "PREFER_NOT")
    assert prefer_not["teacher_id"] == "t_history"

    club_chess = next(a for a in dumped["activities"] if a["id"] == "club_chess")
    assert club_chess["kind"] == "CLUB"
    math_activity = next(a for a in dumped["activities"] if a["id"] == "math")
    assert math_activity["kind"] == "ORDINARY"


def test_tuples_preserve_order_as_json_arrays():
    problem = build_valid_fixture()
    dumped = config_response_from_problem(problem).model_dump(mode="json")

    merged_group = next(g for g in dumped["participant_groups"] if g["id"] == "pg_9a_9b_merged")
    assert merged_group["class_sections"] == ["9a", "9b"]

    math_8a = next(r for r in dumped["teaching_requirements"] if r["id"] == "math_8a")
    assert math_8a["block_policy"]["block_sizes"] == [2, 1, 1, 1]

    history_8a = next(r for r in dumped["teaching_requirements"] if r["id"] == "history_8a")
    assert history_8a["time_preferences"][0]["preferred_periods"] == [0, 1]

    chess_block = next(b for b in dumped["reserved_blocks"] if b["id"] == "club_chess")
    assert chess_block["class_sections"] == ["8a", "8b"]
    assert chess_block["slots"] == [{"day_id": "wed", "period_id": "p8"}]


def test_optional_fields_serialize_as_null_where_appropriate():
    problem = build_valid_fixture()
    dumped = config_response_from_problem(problem).model_dump(mode="json")

    art_8a = next(r for r in dumped["teaching_requirements"] if r["id"] == "art_8a")
    assert art_8a["resource_requirement"] is None
    assert art_8a["split_group_id"] is None
    assert art_8a["distribution_policy"]["min_distinct_days"] is None
    assert art_8a["distribution_policy"]["max_periods_per_day"] is None

    sport_8a = next(r for r in dumped["teaching_requirements"] if r["id"] == "sport_8a")
    assert sport_8a["resource_requirement"] == {"resource_id": "gym"}

    german_8a = next(r for r in dumped["teaching_requirements"] if r["id"] == "german_8a")
    assert german_8a["split_group_id"] == "split_lang_8a"

    club_robotics = next(b for b in dumped["reserved_blocks"] if b["id"] == "club_robotics")
    assert club_robotics["teacher_id"] is None


# -- Phase 3A3.4: schedule generation/read serializers. ------------------


def test_generate_response_has_exact_five_fields_no_entries_no_solver_telemetry():
    version = ActiveScheduleVersion(
        version_number=1, solver_status=SolverStatus.OPTIMAL, total_soft_penalty=3,
        wall_time_seconds=2.5, random_seed=7, created_at=_CREATED_AT,
        entries=(), locked_occurrences=frozenset(),
    )
    response = generate_response_from_active_version(version)
    assert isinstance(response, GenerateScheduleResponse)

    dumped = response.model_dump(mode="json")
    assert set(dumped.keys()) == {"version_number", "solver_status", "total_soft_penalty", "created_at", "is_active"}
    assert dumped["version_number"] == 1
    assert dumped["solver_status"] == "OPTIMAL"
    assert dumped["total_soft_penalty"] == 3
    assert dumped["is_active"] is True
    # Explicitly never present, even though the source ActiveScheduleVersion carries them.
    assert "wall_time_seconds" not in dumped
    assert "random_seed" not in dumped
    assert "entries" not in dumped


def _entry(**overrides) -> ScheduleEntry:
    defaults = dict(
        source=EntrySource.REQUIREMENT, activity_id="math", day_id="mon", period_id="p1",
        class_sections=("9a", "9b"), teacher_id="t1", participant_group_id="g1",
        resource_id=None, requirement_id="req1", reserved_block_id=None,
    )
    defaults.update(overrides)
    return ScheduleEntry(**defaults)


def test_active_schedule_response_preserves_exact_entry_order_and_metadata():
    entries = (
        _entry(requirement_id="req1"),
        _entry(
            source=EntrySource.RESERVED_BLOCK, activity_id="assembly", day_id="tue", period_id="p2",
            class_sections=("9c",), teacher_id=None, participant_group_id=None, requirement_id=None,
            reserved_block_id="block1",
        ),
        _entry(requirement_id="req2", day_id="wed"),
    )
    version = ActiveScheduleVersion(
        version_number=2, solver_status=SolverStatus.FEASIBLE, total_soft_penalty=0,
        wall_time_seconds=1.0, random_seed=None, created_at=_CREATED_AT,
        entries=entries, locked_occurrences=frozenset(),
    )

    response = active_schedule_response_from_active_version(version)
    assert isinstance(response, ActiveScheduleResponse)

    dumped = response.model_dump(mode="json")
    assert set(dumped.keys()) == {
        "version_number", "solver_status", "total_soft_penalty", "created_at", "is_active", "entries",
    }
    assert dumped["is_active"] is True
    assert "wall_time_seconds" not in dumped
    assert "random_seed" not in dumped

    # Order-sensitive proof, not a set/membership comparison.
    assert [e.requirement_id for e in response.entries] == ["req1", None, "req2"]
    assert [e.reserved_block_id for e in response.entries] == [None, "block1", None]
    assert list(reversed(response.entries)) != list(response.entries)


def test_schedule_entry_response_preserves_source_identity_and_optionals():
    requirement_entry = _entry(
        activity_id="math", day_id="mon", period_id="p1", class_sections=("9a", "9b"),
        teacher_id="t1", participant_group_id="g1", resource_id="gym", requirement_id="req1",
    )
    reserved_entry = _entry(
        source=EntrySource.RESERVED_BLOCK, activity_id="assembly", day_id="tue", period_id="p2",
        class_sections=("9c",), teacher_id=None, participant_group_id=None, resource_id=None,
        requirement_id=None, reserved_block_id="block1",
    )

    req_response = schedule_entry_response_from_entry(requirement_entry)
    assert isinstance(req_response, ScheduleEntryResponse)
    assert req_response.source == "REQUIREMENT"
    assert req_response.requirement_id == "req1"
    assert req_response.reserved_block_id is None
    assert req_response.class_sections == ("9a", "9b")

    block_response = schedule_entry_response_from_entry(reserved_entry)
    assert block_response.source == "RESERVED_BLOCK"
    assert block_response.reserved_block_id == "block1"
    assert block_response.requirement_id is None
    assert block_response.teacher_id is None
    assert block_response.participant_group_id is None
    assert block_response.resource_id is None


def test_no_ordinal_or_surrogate_field_exists_on_schedule_schemas():
    for model in (GenerateScheduleResponse, ActiveScheduleResponse, ScheduleEntryResponse):
        fields = set(model.model_fields.keys())
        assert "ordinal" not in fields
        assert "id" not in fields
        assert "schedule_id" not in fields
        assert "schedule_version_id" not in fields


def test_validation_diagnostic_response_from_error():
    error = ValidationError("UNKNOWN_TEACHER", "bad teacher", {"teacher_id": "t9"})
    response = validation_diagnostic_response_from_error(error)
    assert response.code == "UNKNOWN_TEACHER"
    assert response.message == "bad teacher"
    assert response.context == {"teacher_id": "t9"}


# -- Phase 3B.1: class-timetable projection serializer. ------------------


def test_class_timetable_response_preserves_order_and_metadata():
    view = ClassTimetableView(
        school_id="school-1", school_name="Pilot School",
        academic_year_id="year-1", academic_year_label="2025/2026",
        class_section_id="8a", class_section_name="8-A",
        version_number=1, solver_status=SolverStatus.OPTIMAL, total_soft_penalty=3,
        created_at=_CREATED_AT, is_active=True,
        days=(DayHeader(id="mon", name="Monday"), DayHeader(id="tue", name="Tuesday")),
        rows=(
            ClassTimetableRow(
                period_id="p1", period_name="Period 1",
                cells=(
                    ClassTimetableCell(
                        day_id="mon",
                        entries=(
                            ClassTimetableEntry(
                                source=EntrySource.REQUIREMENT, activity_id="german", activity_name="German",
                                teacher_id="t_german", teacher_name="Teacher German",
                                participant_group_id="g_german", participant_group_name="8-A German",
                                requirement_id="german_8a", reserved_block_id=None, resource_id=None,
                            ),
                            ClassTimetableEntry(
                                source=EntrySource.REQUIREMENT, activity_id="russian", activity_name="Russian",
                                teacher_id="t_russian", teacher_name="Teacher Russian",
                                participant_group_id="g_russian", participant_group_name="8-A Russian",
                                requirement_id="russian_8a", reserved_block_id=None, resource_id=None,
                            ),
                        ),
                    ),
                    ClassTimetableCell(day_id="tue", entries=()),
                ),
            ),
        ),
    )

    response = class_timetable_response_from_view(view)
    dumped = response.model_dump(mode="json")

    assert set(dumped.keys()) == {
        "school_id", "school_name", "academic_year_id", "academic_year_label",
        "class_section_id", "class_section_name", "version_number", "solver_status",
        "total_soft_penalty", "created_at", "is_active", "days", "rows",
    }
    assert [d["id"] for d in dumped["days"]] == ["mon", "tue"]
    assert len(dumped["rows"]) == 1
    row = dumped["rows"][0]
    assert row["period_id"] == "p1"
    assert [c["day_id"] for c in row["cells"]] == ["mon", "tue"]

    monday_cell = row["cells"][0]
    assert len(monday_cell["entries"]) == 2
    # Order-sensitive proof, not a set comparison.
    assert [e["requirement_id"] for e in monday_cell["entries"]] == ["german_8a", "russian_8a"]
    assert monday_cell["entries"][0]["activity_name"] == "German"
    assert monday_cell["entries"][1]["activity_name"] == "Russian"

    tuesday_cell = row["cells"][1]
    assert tuesday_cell["entries"] == []


def test_class_timetable_entry_no_ordinal_or_surrogate_field():
    from school_timetable.api.schemas import (
        ClassTimetableEntryResponse,
        ClassTimetableResponse,
        ClassTimetableRowResponse,
    )

    for model in (ClassTimetableEntryResponse, ClassTimetableRowResponse, ClassTimetableResponse):
        fields = set(model.model_fields.keys())
        assert "ordinal" not in fields
        assert "id" not in fields
        assert "wall_time_seconds" not in fields
        assert "random_seed" not in fields
        assert "resource_name" not in fields
