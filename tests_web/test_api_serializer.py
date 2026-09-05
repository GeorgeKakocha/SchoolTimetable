"""Pure, DB-free unit tests for the domain -> API serializer
(Phase 3A2.4, `api/serializer.py`). No database, no FastAPI TestClient
-- these isolate serialization failures from repository/DB failures,
which `test_config_api.py` covers separately."""
from __future__ import annotations

from school_timetable.api.serializer import config_response_from_problem
from school_timetable.fixtures.valid_fixture import build_valid_fixture


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
