from school_timetable.fixtures.school_scale.assemble import (
    AssembledSchoolScaleFixture,
    assemble,
    build_problem_from_curriculum,
)
from school_timetable.fixtures.school_scale.consistency import (
    OccupancyConsistencyReport,
    check_class_occupancy_consistency,
)
from school_timetable.fixtures.school_scale.constructor import FixtureGenerationError
from school_timetable.fixtures.school_scale.curriculum import Curriculum, build_curriculum
from school_timetable.fixtures.school_scale.scenarios import (
    build_school_scale_impossible,
    build_school_scale_standard,
    build_school_scale_tight,
)

__all__ = [
    "AssembledSchoolScaleFixture",
    "assemble",
    "build_problem_from_curriculum",
    "OccupancyConsistencyReport",
    "check_class_occupancy_consistency",
    "FixtureGenerationError",
    "Curriculum",
    "build_curriculum",
    "build_school_scale_standard",
    "build_school_scale_tight",
    "build_school_scale_impossible",
]
