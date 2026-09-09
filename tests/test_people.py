import pytest

from school_timetable.domain.people import Teacher


def test_full_name_joins_first_and_last_with_one_space():
    teacher = Teacher(id="t1", first_name="Nino", last_name="Beridze")
    assert teacher.full_name == "Nino Beridze"


def test_full_name_with_empty_last_name_is_first_name_only():
    teacher = Teacher(id="t_math", first_name="Teacher Math", last_name="")
    assert teacher.full_name == "Teacher Math"


def test_full_name_never_leaves_trailing_or_double_space():
    teacher = Teacher(id="t1", first_name=" Nino ", last_name="  ")
    assert teacher.full_name == "Nino"
    assert not teacher.full_name.endswith(" ")
    assert "  " not in teacher.full_name


def test_last_name_is_a_required_constructor_argument():
    with pytest.raises(TypeError):
        Teacher(id="t_history", first_name="Teacher History")


def test_pre_migration_synthetic_names_survive_unchanged():
    for original_name in ("Teacher Math", "Teacher History", "Teacher German"):
        teacher = Teacher(id="t_x", first_name=original_name, last_name="")
        assert teacher.full_name == original_name
