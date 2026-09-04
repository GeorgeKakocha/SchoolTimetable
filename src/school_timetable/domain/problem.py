"""The complete scheduling problem: everything a school supplies as input."""
from __future__ import annotations

from dataclasses import dataclass, field

from school_timetable.domain.activities import Activity
from school_timetable.domain.blocks import FixedPlacement, ReservedBlock
from school_timetable.domain.calendar import AcademicYear, Day, Period
from school_timetable.domain.groups import ClassSection, ParticipantGroup
from school_timetable.domain.people import Teacher, TeacherAvailability
from school_timetable.domain.requirements import TeachingRequirement
from school_timetable.domain.resources import Resource
from school_timetable.domain.school import School


@dataclass(frozen=True)
class SchedulingProblem:
    school: School
    academic_year: AcademicYear
    days: tuple[Day, ...]
    periods: tuple[Period, ...]
    teachers: tuple[Teacher, ...]
    class_sections: tuple[ClassSection, ...]
    participant_groups: tuple[ParticipantGroup, ...]
    activities: tuple[Activity, ...]
    teaching_requirements: tuple[TeachingRequirement, ...]
    resources: tuple[Resource, ...] = ()
    teacher_availabilities: tuple[TeacherAvailability, ...] = ()
    reserved_blocks: tuple[ReservedBlock, ...] = ()
    fixed_placements: tuple[FixedPlacement, ...] = ()
